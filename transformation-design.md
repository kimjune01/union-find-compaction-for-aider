# Transformation Design: Union-Find Chat History for Aider

## Why

Users who build up long chat sessions have one escape valve: `/clear`. It wipes everything. There's no way to see what the model remembers, no way to drop one stale topic without losing the rest.

This isn't speculation. Issue research on `paul-gauthier/aider` (~73 issues, 235+ comments) surfaced the same gap from multiple angles:

- **#3607** (selective history control) — "I want to drop the debugging context but keep the refactor decisions"
- **#2219** (see/edit context) — "I want to see what the model's context actually contains"
- **#948** (token breakdown with actions) — "show me what's consuming tokens and let me act on it"
- **#4079** (cross-session persistence) — "topics should survive across sessions"

The current summarizer can't support this. It produces a single text blob: "I spoke to you previously about a number of things..." No internal boundaries, no topic markers, no structure to inspect or remove. You'd have to re-parse the summary to guess at topics, which is fragile and circular.

Union-find compaction maintains topic clusters as a byproduct of compression. Each cluster has its own summary. The experiment (17 conversations, 136 paired observations, McNemar p=0.248) established that switching backends doesn't degrade quality. The value proposition is not "better summaries" — it's "structured context enables visibility and selective control."

## Delivery: Two PRs

**PR 1 — Foundation (this design doc's primary scope):** Port 4 modules, add `--chat-history-summarizer union-find` flag, construction conditional. No new commands, no new UX. Pitch: opt-in alternative backend, quality-equivalent, default unchanged.

**PR 2 — Feature (follow-up after PR 1 merges):** `/topics` and `/drop-topic` commands. Builds on the structured representation PR 1 provides.

---

# PR 1: Foundation

## What Exists

Four modules, 145 tests, all passing.

### `src/context_window.py` — Forest + ContextWindow

**Forest** — dict-based union-find cluster store.

```python
class Forest:
    def __init__(self, summarizer):
        self._parent = {}        # node_id → parent_id
        self._content = {}       # node_id → raw content
        self._embedding = {}     # node_id → embedding vector
        self._summary = {}       # root_id → cached summary
        self._dirty = set()      # dirty root ids
        self._dirty_inputs = {}  # root_id → texts to summarize
        self._children = {}      # root_id → set of child ids
        self._root_order = []    # insertion-order tracking

    def insert(msg_id, content, embedding)   # create singleton
    def union(id_a, id_b)                    # synchronous merge, weighted centroid
    def resolve_dirty()                       # one summarizer call per dirty root
    def compact(node_id)                      # cached summary or raw content
    def nearest_root(embedding)               # closest cluster by cosine
    def roots()                               # all current root ids, insertion order
    def cluster_count()                       # len(roots())
```

**ContextWindow** — hot zone + cold forest.

```python
class ContextWindow:
    def __init__(self, embedder, summarizer,
                 graduate_at=26,
                 max_cold_clusters=10, merge_threshold=0.15):

    def append(content)       # embed, push to hot, graduate/evict
    def render()              # cold summaries + hot contents
    def resolve_dirty()       # delegate to forest
    hot_count                 # messages not yet graduated
    cold_count                # cluster count
```

Graduation: token-aware. When `too_big` fires, keep only as many recent hot messages as fit in 25% of `max_tokens`. The rest graduate to forest. Merges with nearest cluster if cosine >= 0.15. Force-merges closest pair if cluster count > 10. The fixed `graduate_at=26` from gemini-cli was replaced after manual testing revealed a control-loop deadlock (see DESIGN_DECISIONS.md #16).

Eviction: when hot exceeds `evict_at`, oldest evicted. Overlap window (~4 messages) gives `resolve_dirty()` time to run.

### `src/embedding_service.py` — TFIDFEmbedder

Pure Python, no dependencies. `embed(text)` → sparse float vector. Tokenize (lowercase, split, filter stopwords), compute TF-IDF. Vocabulary grows incrementally.

### `src/cluster_summarizer.py` — ClusterSummarizer

```python
class ClusterSummarizer:
    def __init__(self, models):
        self.models = models if isinstance(models, list) else [models]

    def summarize(self, texts):
        # model cascade: try weak_model first, fallback to main_model
        # prompt: summarize fragments, preserve filenames/functions/errors
        # first-person user voice, no code blocks
```

### `src/chat_summary_uf.py` — ChatSummaryUF(ChatSummary)

Drop-in subclass. Fits inside `summarize_start/worker/end` lifecycle unchanged.

```python
class ChatSummaryUF(ChatSummary):
    def __init__(self, models=None, max_tokens=1024):
        super().__init__(models, max_tokens)
        self._fed_count = 0
        self._init_context_window()

    def summarize(self, messages, depth=0):
        # 1. Skip if not too_big
        # 2. Stale detection: if _fed_count > len(messages), rebuild
        # 3. Feed only new user/assistant messages
        # 4. resolve_dirty() — blocking LLM work
        # 5. render() — uses fresh summaries
        # 6. Format: [summary_msg, "Ok.", *hot_messages]
        # 7. Budget safety: fallback to recursive if inflated
```

### Construction site

```python
# aider/args.py
parser.add_argument(
    "--chat-history-summarizer",
    default="recursive",
    choices=["recursive", "union-find"],
)

# aider/main.py
if getattr(args, 'chat_history_summarizer', None) == 'union-find':
    summarizer = ChatSummaryUF(models, max_tokens)
else:
    summarizer = ChatSummary(models, max_tokens)
```

## PR 1 Changes to Foundation Code

Two fixes applied before porting. Both are backend correctness, not UX.

### 1. Stable root ordering

**Problem:** `roots()` returns `list(set(...))` — nondeterministic. Downstream operations (rendering, future `/topics` numbering) depend on deterministic ordering.

**Fix:** Add `_root_order` list to Forest. Track insertion order. `roots()` returns roots in the order their first member was inserted.

```python
class Forest:
    def __init__(self, summarizer):
        # ... existing fields ...
        self._root_order = []    # insertion-order tracking

    def insert(self, msg_id, content, embedding):
        # ... existing logic ...
        self._root_order.append(msg_id)

    def union(self, id_a, id_b):
        root_a = self._find(id_a)
        root_b = self._find(id_b)
        if root_a == root_b:
            return root_a

        # ... existing merge logic ...

        # Update root order: remove old_root, keep new_root's position
        if old_root in self._root_order:
            self._root_order.remove(old_root)

        return new_root

    def roots(self):
        """Return roots in stable insertion order."""
        seen = set()
        ordered = []
        for node_id in self._root_order:
            root = self._find(node_id)
            if root not in seen:
                seen.add(root)
                ordered.append(root)
        return ordered
```

### 2. Weighted centroid averaging

**Problem:** `union()` averages centroids with equal weight. A 1-message cluster pulls a 50-message cluster's centroid halfway. Over repeated merges, this distorts cluster identity.

**Fix:** Weight by cluster size.

```python
def union(self, id_a, id_b):
    # ... after determining new_root, old_root ...

    size_new = len(self._children.get(new_root, set()))
    size_old = len(self._children.get(old_root, set()))
    total = size_new + size_old

    emb_a = self._embedding.get(new_root, {})
    emb_b = self._embedding.get(old_root, {})
    if emb_a and emb_b:
        if isinstance(emb_a, dict) and isinstance(emb_b, dict):
            all_keys = set(emb_a.keys()) | set(emb_b.keys())
            self._embedding[new_root] = {
                k: (emb_a.get(k, 0.0) * size_new + emb_b.get(k, 0.0) * size_old) / total
                for k in all_keys
            }
        else:
            self._embedding[new_root] = [
                (a * size_new + b * size_old) / total
                for a, b in zip(emb_a, emb_b)
            ]
```

**Note:** `size_new` and `size_old` are computed before the `_children` merge, so they reflect pre-merge sizes.

## PR 1 Test Plan

| Area | What's tested |
|------|---------------|
| Flag selection | `--chat-history-summarizer union-find` constructs `ChatSummaryUF`; default constructs `ChatSummary` |
| Default unchanged | No code path changes when flag is absent or `recursive` |
| Output format | `summarize()` returns `[summary_msg, "Ok.", *hot_messages]` matching recursive |
| Fallback to recursive | Result exceeding `max_tokens` triggers `super().summarize()`; result >= input tokens triggers fallback |
| `summarize_all()` parity | Delegates to `super().summarize_all()`, same result as recursive |
| Stale discard + rebuild | When `done_messages` changes during summarization, result discarded; next call rebuilds forest via `_fed_count` |
| Low token budget | `max_chat_history_tokens=1024` with 10 clusters triggers fallback gracefully |
| Weighted centroid | Merging unequal clusters weights toward larger; equal-size merge at midpoint |
| Stable root ordering | Roots in insertion order after inserts; order preserved after merges; deterministic across calls |
| Cluster summarization | Model cascade (weak first, fallback to main), same `simple_send_with_retries()` |
| Incremental feeding | `_fed_count` feeds only new messages; shrink triggers rebuild |
| Forest mechanics | Insert, union, roots, compact, cluster_count — existing 145 tests |

## Aider Integration Points (PR 1)

| File | Change | Lines |
|------|--------|-------|
| `aider/args.py` | Add `--chat-history-summarizer` argument | ~5 |
| `aider/main.py` | Conditional at summarizer construction | ~4 |
| `aider/context_window.py` | New file (ported from src/) | ~340 |
| `aider/embedding_service.py` | New file (ported from src/) | ~80 |
| `aider/cluster_summarizer.py` | New file (ported from src/) | ~60 |
| `aider/chat_summary_uf.py` | New file (ported from src/) | ~90 |

Imports change from standalone (`from context_window import ...`) to aider-internal (`from aider.context_window import ...`).

## What Doesn't Change (PR 1)

- Default summarization (recursive, unchanged)
- Threading model (`summarize_start/worker/end`)
- Output format (`[summary_msg, "Ok.", *hot_messages]`)
- `summarize_all()` behavior
- Existing commands (`/clear`, `/drop`, `/tokens`, `/reset`)
- Existing tests (no modifications)

---

# PR 2: `/topics` and `/drop-topic`

_Depends on PR 1 being merged. Everything below builds on the foundation._

## Source of Truth

`done_messages` is aider's source of truth — it's what gets sent to the model. The forest is a shadow structure that provides topic visibility and selective drop. Both are updated in lockstep:

- `summarize()` feeds messages into forest, renders, writes result to `done_messages` (existing flow)
- `/drop-topic` removes from forest, re-renders, writes result to `done_messages` (same pattern as `/clear`)
- `/topics` reads from forest (read-only)

The forest is rebuilt after every successful summarization. When `summarize_end()` swaps in the result, `done_messages` shrinks. Next `summarize()` call sees `_fed_count > len(messages)` → full rebuild. The forest is a session-scoped cache, not a persistent store. Topics are session-scoped.

## PR 2 Changes

### 1. `remove_cluster(root_id)` — new Forest method

Removes a root and all its children from every data structure.

```python
def remove_cluster(self, root_id):
    """Remove a cluster by root id. Returns list of removed node ids."""
    root = self._find(root_id)
    members = list(self._children.get(root, {root}))

    for node_id in members:
        self._parent.pop(node_id, None)
        self._content.pop(node_id, None)
        self._embedding.pop(node_id, None)

    self._summary.pop(root, None)
    self._dirty.discard(root)
    self._dirty_inputs.pop(root, None)
    self._children.pop(root, None)

    # Update root order
    self._root_order = [r for r in self._root_order if r not in members]

    return members
```

~15 lines. Returns removed node IDs for confirmation messaging.

### 2. `cmd_topics(self, args)` — in `aider/commands.py`

```python
def cmd_topics(self, args):
    """Show topic clusters in compressed chat history."""
    summarizer = self.coder.summarizer
    if not isinstance(summarizer, ChatSummaryUF):
        self.io.tool_output(
            "Topic view requires --chat-history-summarizer union-find."
        )
        return

    # Threading guard — forest reads can race with background summarizer
    if self.coder.summarizer_thread is not None:
        self.io.tool_output(
            "Summarization is running. Try again in a moment."
        )
        return

    cw = summarizer.context_window
    forest = cw._forest
    roots = forest.roots()

    if not roots:
        self.io.tool_output("No topics yet (history not compressed).")
        return

    self.io.tool_output("\nChat history topics:\n")
    for i, root in enumerate(roots, 1):
        summary = forest.compact(root)
        tokens = summarizer.token_count({"role": "user", "content": summary})
        # First line of summary, truncated
        preview = summary.split("\n")[0][:80]
        self.io.tool_output(f"  {i}. {tokens:>5} tokens — \"{preview}\"")

    hot = cw.hot_count
    if hot > 0:
        hot_msgs = cw._hot[cw._graduated_index:]
        hot_tokens = sum(
            summarizer.token_count({"role": "user", "content": c})
            for c, _e in hot_msgs
        )
        self.io.tool_output(f"  + {hot_tokens} tokens — {hot} recent messages (not yet compressed)")

    total = sum(
        summarizer.token_count({"role": "user", "content": forest.compact(r)})
        for r in roots
    )
    if hot > 0:
        total += hot_tokens
    self.io.tool_output(f"\nTotal: {total:,} tokens")
```

~30 lines. Accesses `self.coder.summarizer` — same pattern as existing commands that read coder state.

### 3. `cmd_drop_topic(self, args)` — in `aider/commands.py`

```python
def cmd_drop_topic(self, args):
    """Drop a topic cluster from compressed chat history."""
    summarizer = self.coder.summarizer
    if not isinstance(summarizer, ChatSummaryUF):
        self.io.tool_output(
            "Topic dropping requires --chat-history-summarizer union-find."
        )
        return

    # Threading guard
    if self.coder.summarizer_thread is not None:
        self.io.tool_output(
            "Can't drop topics while summarization is running. Try again in a moment."
        )
        return

    try:
        index = int(args.strip())
    except (ValueError, AttributeError):
        self.io.tool_error("Usage: /drop-topic N (where N is the topic number from /topics)")
        return

    cw = summarizer.context_window
    forest = cw._forest
    roots = forest.roots()

    if index < 1 or index > len(roots):
        self.io.tool_error(f"Invalid topic number. Use /topics to see available topics (1-{len(roots)}).")
        return

    root = roots[index - 1]
    summary = forest.compact(root)
    tokens = summarizer.token_count({"role": "user", "content": summary})

    # 1. Remove from forest (shadow structure)
    forest.remove_cluster(root)

    # 2. Re-render and update done_messages (source of truth)
    rendered = cw.render()
    if rendered:
        cold_parts = rendered[:-cw.hot_count] if cw.hot_count > 0 else rendered
        summary_text = prompts.summary_prefix + "\n\n".join(cold_parts)
        hot_messages = list(self.coder.done_messages[-cw.hot_count:]) if cw.hot_count > 0 else []
        self.coder.done_messages = [
            {"role": "user", "content": summary_text},
            {"role": "assistant", "content": "Ok."},
            *hot_messages,
        ]
    else:
        self.coder.done_messages = []

    self.io.tool_output(f"Dropped topic {index} ({tokens:,} tokens freed).")
```

~35 lines. The re-render + `done_messages` update follows the same pattern as `/clear` (which sets `done_messages = []`). The model immediately stops seeing the dropped topic.

### Threading safety (PR 2)

Both `/topics` and `/drop-topic` access forest state. The background summarizer also reads and writes forest state via `summarize_worker`.

**Guard pattern (both commands):**

1. Commands run on the main thread. `summarize_worker` runs on a background thread.
2. Both `cmd_topics` and `cmd_drop_topic` check `self.coder.summarizer_thread is not None`. If a thread is running, refuse with "try again in a moment."
3. `/drop-topic` additionally updates `done_messages` after the forest mutation. Since the thread guard ensures no concurrent summarization, `done_messages` is safe to write.
4. The stale `_fed_count` naturally triggers a forest rebuild on the next `summarize()` call.

**Decision:** Refuse both commands during summarization. No thread join (would block the UI). No lock (forest isn't designed for concurrent access, and the guard makes it unnecessary).

### Exposing `context_window` from ChatSummaryUF

`cmd_topics` and `cmd_drop_topic` need access to the `ContextWindow` instance. Add a public property:

```python
class ChatSummaryUF(ChatSummary):
    @property
    def context_window(self):
        return self._context_window
```

### `/topics` with recursive summarizer

**Decision:** Show a guidance message, not a degraded view. "Topic view requires --chat-history-summarizer union-find." A degraded view (token count without breakdown) would duplicate `/tokens` and confuse the mental model.

### Flag name

**Decision:** `--chat-history-summarizer`. Matches aider's naming convention (`--chat-history-token-limit`, `--chat-history-file`). Choices: `recursive` (default), `union-find`.

## PR 2 Tests

### `test_remove_cluster`

- Removes root and all children from `_parent`, `_content`, `_embedding`
- Removes from `_summary`, `_dirty`, `_dirty_inputs`, `_children`
- Removes from `_root_order`
- Returns list of removed node IDs
- `cluster_count()` decreases by 1
- Removed content doesn't appear in `render()`

### `test_cmd_topics`

- Shows indexed list with token counts and preview text
- Shows hot zone count and tokens
- Shows "No topics yet" when history empty
- Shows guidance message when summarizer is recursive
- Shows total token count
- Refused when `summarizer_thread is not None`

### `test_cmd_drop_topic`

- Drop removes cluster from forest, frees tokens
- Drop updates `done_messages` immediately (source of truth)
- `done_messages` after drop does not contain the dropped topic's summary
- Drop with invalid index shows error — `done_messages` unchanged
- Drop with non-integer shows usage message — `done_messages` unchanged
- Subsequent `/topics` reflects the removal
- Drop refused when `summarizer_thread is not None` — both forest and `done_messages` unchanged
- Drop succeeds after thread completes (set to None)
- After drop, `done_messages = []` when all topics removed

### `test_drop_topic_done_messages_sync`

- Integration test: drop topic, verify `done_messages` matches `render()` output
- After drop, next `summarize()` call triggers rebuild (`_fed_count > len(messages)`)
- Rebuilt forest does not contain the dropped topic
- Model prompt (assembled from `done_messages`) excludes dropped content

## PR 2 Integration Points

| File | Change | Lines |
|------|--------|-------|
| `aider/commands.py` | `cmd_topics()`, `cmd_drop_topic()` methods | ~65 |
| `aider/context_window.py` | `remove_cluster()` method, `context_window` property | ~20 |

---

## Follow-up (not in either PR)

- Cross-session topic persistence (#4079) — requires serializing the forest, separate PR
