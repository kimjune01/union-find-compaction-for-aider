# Transformation Design: Union-Find Context Compaction for Aider

v2 architecture from day one. Structural lessons from gemini-cli applied:
- `append()` synchronous — no LLM calls
- `render()` synchronous — cached summaries + hot zone
- `resolveDirty()` async — batch-summarize dirty clusters in background
- Overlap window — graduated messages stay in hot zone until background resolves

**Note on parameters:** Default values (`graduate_at=26`, `evict_at=30`, `max_cold_clusters=10`, `merge_threshold=0.15`) are starting points carried from gemini-cli v2. They have not been validated on aider's conversation distribution. Aider-specific tuning is expected during the experiment phase. The architectural properties (non-blocking append, deferred summarization, incremental clustering) transfer regardless of parameter values.

## Target Architecture

```
append(msg)       <1ms   Synchronous. TF-IDF embed, push to hot, graduate if overflow.
render()          <1ms   Synchronous. Cached cluster summaries + hot zone verbatim.
resolveDirty()    ~4s    Async. Batch-summarize dirty clusters.
```

### Integration with aider's threading model

```
move_back_cur_messages()
  → done_messages += cur_messages
  → summarize_start()
      → summarizer.too_big(done_messages)?
      → Thread(target=summarize_worker)
          → summarizer.summarize(done_messages)
              → ChatSummaryUF.summarize():
                  1. Feed all messages into context_window.append()
                  2. Resolve: context_window.resolveDirty()  ← blocking LLM work
                  3. Render: context_window.render()          ← uses fresh summaries
                  4. Format as aider messages and return
  → User types next message (concurrent)
  → summarize_end()
      → thread.join()
      → if not stale: done_messages = result
```

The existing `summarize_start/worker/end` lifecycle works unchanged. `ChatSummaryUF.summarize()` runs inside the worker thread. `resolveDirty()` is the only blocking LLM work, and it runs in the background thread — not the main thread.

## New Files

### `src/context_window.py`

Core data structures. Direct port from gemini-cli's `contextWindow.ts`, adapted for Python.

#### `cosine_similarity(a, b) → float`

```python
def cosine_similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
```

#### `find_closest_pair(forest) → tuple or None`

O(n^2) on cluster count. With max 10 clusters, this is 45 comparisons — negligible.

#### `class Forest`

State:
- `_nodes`: dict mapping msg_id → Message
- `_summaries`: dict mapping root_id → summary string
- `_children`: dict mapping root_id → list of member msg_ids
- `_centroids`: dict mapping root_id → embedding vector
- `_dirty_inputs`: dict mapping root_id → list of strings to summarize

Methods:
- `insert(msg_id, content, embedding, timestamp)` — create singleton cluster
- `find(msg_id) → root_id` — with path compression
- `union(id_a, id_b) → root_id` — **synchronous**, structural merge only. Collects dirty inputs. No LLM calls.
- `resolve_dirty()` — **async**. One `summarizer.summarize()` call per dirty root.
- `compact(root_id) → str` — cached summary or raw content for singletons
- `expand(root_id) → list[str]` — all member raw contents
- `nearest(query_embedding, k, min_sim) → list[root_id]` — top-k by cosine similarity
- `nearest_root(query_embedding) → (root_id, sim) or None`
- `roots() → list[root_id]`
- `is_dirty(root_id) → bool`
- `dirty_roots() → list[root_id]`
- `cluster_count() → int`
- `size() → int`

**union() dirty input collection:**

When merging A and B:
1. If A has dirty inputs, use those. Else if A has a summary, use `[summary]`. Else use `[raw_content]`.
2. Same for B.
3. Merged dirty inputs = inputs_A + inputs_B.
4. This ensures `resolve_dirty()` sees either the previous clean summary or the raw content — never re-reads all historical members (O(n) not O(n^2)).

#### `class ContextWindow`

Parameters:
- `graduate_at`: default 26 — start inserting into forest when ungraduated > this
- `evict_at`: default 30 — remove from hot zone when hot > this
- `max_cold_clusters`: default 10 — force-merge closest pair if exceeded
- `merge_threshold`: default 0.15 — minimum cosine similarity to merge on graduation

State:
- `_hot`: list of Message
- `_graduated_index`: int tracking how many hot messages have been graduated
- `_next_id`: int auto-incrementing message ID
- `_forest`: Forest instance

Methods:
- `append(content, timestamp=None) → msg_id` — **synchronous**. Embed, push to hot, graduate overflow, evict overflow.
- `render(query=None, k=3, min_sim=0.05) → list[str]` — **synchronous**. Cold summaries + hot contents.
- `resolve_dirty()` — **async**. Delegates to `forest.resolve_dirty()`.
- `expand(root_id) → list[str]`
- Properties: `hot_count`, `cold_cluster_count`, `total_messages`

**Graduation logic (synchronous):**
```python
while len(self._hot) - self._graduated_index > self._graduate_at:
    self._graduate(self._hot[self._graduated_index])
    self._graduated_index += 1
```

**Eviction logic (synchronous):**
```python
while len(self._hot) > self._evict_at:
    self._hot.pop(0)
    self._graduated_index -= 1
```

**_graduate(msg) — synchronous:**
1. `forest.insert(msg_id, content, embedding)`
2. Find nearest existing cluster (excluding self)
3. If similarity >= threshold: `forest.union(msg_id, nearest_root)`
4. While `cluster_count > max_cold_clusters`: `union(closest_pair)`

### `src/embedding_service.py`

#### `class TFIDFEmbedder`

Incremental TF-IDF. No external dependencies.

State:
- `_vocab`: dict mapping term → index
- `_df`: dict mapping term → document frequency
- `_doc_count`: int

Methods:
- `embed(text) → list[float]` — tokenize, update vocab/df, compute TF-IDF vector

**Tokenization:** lowercase, split on non-alphanumeric, filter stopwords.

**TF:** term frequency in this document (count / total terms).

**IDF:** `log(doc_count / df[term])`. Grows vocabulary incrementally — new terms get new dimensions.

**Embedding stability:** The TF-IDF vector space evolves as vocabulary grows (new dimensions added, IDF weights shift). This means older embeddings and centroids are computed in a slightly different space than newer ones. In practice this is acceptable because: (a) the forest is rebuilt from scratch whenever a summary result is applied (stale detection resets `_fed_count`), so stale embeddings don't accumulate indefinitely; (b) centroids are recomputed on merge via weighted average of current vectors; (c) with only 10 clusters and a 0.15 similarity threshold, small IDF drift doesn't change clustering decisions. If drift proves problematic, the fix is to re-embed all centroids after each vocabulary update — O(k) with k=10, negligible cost.

### `src/cluster_summarizer.py`

#### `class ClusterSummarizer`

Wraps aider's model API for cluster summarization.

```python
class ClusterSummarizer:
    def __init__(self, models):
        self.models = models if isinstance(models, list) else [models]

    def summarize(self, texts):
        content = "\n\n---\n\n".join(texts)
        messages = [
            {"role": "system", "content": CLUSTER_SUMMARIZE_PROMPT},
            {"role": "user", "content": content},
        ]
        for model in self.models:
            try:
                result = model.simple_send_with_retries(messages)
                if result is not None:
                    return result
            except Exception as e:
                print(f"Cluster summarization failed for model {model.name}: {str(e)}")
                continue
        raise ValueError("cluster summarizer unexpectedly failed for all models")
```

**Prompt:**
```
Summarize the following conversation fragments into a concise summary.
The first item may be a previous summary — integrate it with the new messages.
Preserve specific technical details: file paths, function names, error messages,
configuration values, version numbers.
Write in first person as the user ("I asked you...", "We discussed...").
Do not include code blocks. Do not conclude the summary.
```

**Note:** Matches aider's convention of user-voiced summaries. The prompt mirrors `prompts.summarize` but is adapted for cluster fragments rather than full conversation history.

**Model cascade:** Same as current system — tries `weak_model` first, falls back to `main_model`. Uses the same `models` list passed to ChatSummaryUF.

### `src/chat_summary_uf.py`

#### `class ChatSummaryUF(ChatSummary)`

Drop-in replacement. Subclasses `ChatSummary` from `aider.history`.

```python
from aider.history import ChatSummary
from aider import prompts

class ChatSummaryUF(ChatSummary):
    def __init__(self, models=None, max_tokens=1024):
        super().__init__(models, max_tokens)
        self._fed_count = 0
        self._init_context_window()

    def _init_context_window(self):
        self.context_window = ContextWindow(
            embedder=TFIDFEmbedder(),
            summarizer=ClusterSummarizer(self.models),
            graduate_at=26,
            evict_at=30,
            max_cold_clusters=10,
            merge_threshold=0.15,
        )
        self._fed_count = 0

    def summarize(self, messages, depth=0):
        if not self.too_big(messages):
            return messages

        # Stale detection: if messages shrank, previous result was applied.
        # The forest was built from pre-summary messages that no longer exist.
        # Rebuild from the new (shorter) message list.
        if self._fed_count > len(messages):
            self._init_context_window()

        # Feed only new user/assistant messages (incremental across calls)
        # Skip system messages — matches current summarize_all() which only
        # processes USER and ASSISTANT roles.
        for msg in messages[self._fed_count:]:
            role = msg.get("role", "").upper()
            if role not in ("USER", "ASSISTANT"):
                continue
            content = msg.get("content", "")
            if content:
                self.context_window.append(f"# {role}\n{content}")
        self._fed_count = len(messages)

        # Resolve dirty clusters first so render() returns fresh summaries.
        # Blocks in worker thread, not main thread.
        self.context_window.resolve_dirty()

        # Render: resolved summaries + hot zone (synchronous)
        rendered = self.context_window.render()

        # Split rendered into cold (cluster summaries) and hot (recent)
        hot_count = self.context_window.hot_count
        if hot_count > 0 and hot_count < len(rendered):
            cold_parts = rendered[:-hot_count]
            summary_text = prompts.summary_prefix + "\n\n".join(cold_parts)
            hot_messages = messages[-hot_count:]
            result = [
                {"role": "user", "content": summary_text},
                {"role": "assistant", "content": "Ok."},
                *hot_messages,
            ]
        else:
            return messages

        # Token budget check: must fit within max_tokens
        result_tokens = sum(self.token_count(m) for m in result)
        if result_tokens > self.max_tokens:
            return super().summarize(messages, depth)
        # Also verify compression actually helped
        input_tokens = sum(self.token_count(m) for m in messages)
        if result_tokens >= input_tokens:
            return super().summarize(messages, depth)

        # Ensure ends with assistant message (matches parent's contract)
        if result and result[-1]["role"] != "assistant":
            result.append({"role": "assistant", "content": "Ok."})

        return result

    def summarize_all(self, messages):
        # Used by Coder.create() during edit format transitions.
        # Delegates to parent — format transitions need a single summary blob.
        return super().summarize_all(messages)
```

**Key design decisions in this class:**

1. **Incremental feeding with stale detection.** `_fed_count` tracks how many messages have been fed to the forest. On each call, only new messages are appended. If `_fed_count > len(messages)`, it means the previous summarized result was applied (done_messages shrank), so the forest is rebuilt from scratch. If the previous result was discarded (done_messages grew), the forest is still valid and only the new messages need feeding.

2. **Forest persists across calls.** When `summarize_end()` discards a result (stale), the forest retains its clustering state. The next call feeds the additional messages incrementally. Cross-call clustering is preserved. Topics accumulate structure over multiple summarization attempts.

3. **Mandatory token budget check.** Two checks: (a) if the result exceeds `max_tokens`, fall back to recursive; (b) if the result isn't smaller than the input (token inflation), fall back to recursive. This preserves the current system's budget guarantee as a hard safety net.

4. **`resolveDirty()` blocks in the worker thread.** The worker thread is already a background thread launched by `summarize_start()`. Blocking here means the main thread continues normally — the user is not blocked. However, `summarize_end()` joins the worker thread before the next LLM call, so a fast-typing user can still block there. This is the same blocking behavior as the current recursive system, not worse.

5. **`summarize_all()` delegates to parent.** Edit format transitions in `Coder.create()` need a single summary, not cluster structure. The parent's `summarize_all()` returns `[{"role": "user", "content": summary}]` — one user message. The `summarize()` wrapper in the parent then appends `{"role": "assistant", "content": "Ok."}` if needed.

## Construction Site

In `main.py`, the summarizer is constructed and passed to the coder:

```python
# Current
summarizer = ChatSummary(
    [main_model.weak_model, main_model],
    args.max_chat_history_tokens or main_model.max_chat_history_tokens,
)

# With union-find option
if getattr(args, 'chat_history_summarizer', None) == 'union-find':
    summarizer = ChatSummaryUF(
        [main_model.weak_model, main_model],
        args.max_chat_history_tokens or main_model.max_chat_history_tokens,
    )
else:
    summarizer = ChatSummary(
        [main_model.weak_model, main_model],
        args.max_chat_history_tokens or main_model.max_chat_history_tokens,
    )
```

New CLI arg in `args.py`:
```python
parser.add_argument(
    "--chat-history-summarizer",
    default="recursive",
    choices=["recursive", "union-find"],
    help="Algorithm for chat history summarization (default: recursive)",
)
```

## What Stays

- **`summarize_start/worker/end` lifecycle** — unchanged, union-find runs inside worker thread
- **`too_big()` check** — inherited from ChatSummary, uses same token counting
- **Model cascade** — same `[weak_model, main_model]` list
- **Summary prefix** — `prompts.summary_prefix` reused for cold zone output
- **`summarize_all()`** — delegates to parent, used for edit format transitions
- **Stale-safety** — `summarize_end()` identity check works unchanged
- **Background threading** — existing `threading.Thread` infrastructure

## What Changes

- **Compression algorithm** — recursive split → union-find clustering
- **Summary granularity** — single blob → per-cluster summaries
- **Split strategy** — token-count boundary → semantic similarity
- **Output structure** — `[summary]` (+ parent appends ok) → `[cluster_summaries, ok, *hot_messages]`
- **LLM call pattern** — 1-4 large calls → many small calls
- **New dependency** — TF-IDF embedding (no external packages, pure Python)

## Token Budget Compliance

The current system guarantees compliance via recursion: if result still too big, recurse until it fits.

Union-find uses structural bounds as primary enforcement:
- Cold zone: bounded by `max_cold_clusters × max_summary_size`. With 10 clusters and ~200 token summaries, cold ≈ 2,000 tokens.
- Hot zone: bounded by `evict_at` messages. With 30 messages averaging ~200 tokens, hot ≈ 6,000 tokens.
- Total estimate: ~8,000 tokens for compressed history. Well within typical `max_chat_history_tokens` (usually 10,000-40,000).

**Hard safety net:** After rendering, `summarize()` counts output tokens and checks two conditions: (1) does the result exceed `max_tokens`? (2) is the result not smaller than the input? If either is true, it falls back to `super().summarize()` — the parent's recursive algorithm. This guarantees the union-find path never produces a worse result than the current system.

This two-layer approach (structural bounds + mandatory fallback) preserves the current system's budget guarantee while allowing the clustering approach to operate freely within those bounds.

## Testing Strategy

### Unit Tests (`test_context_window.py`)

Forest:
- `insert()` creates singleton with correct embedding
- `find()` returns self for singleton, root for merged
- `union()` is synchronous — does not call summarizer
- `union()` collects dirty inputs correctly
- `resolve_dirty()` calls summarizer once per dirty root
- `compact()` returns raw content for singletons, summary for resolved
- `expand()` returns all member contents
- `nearest()` returns top-k by cosine similarity

ContextWindow:
- `append()` is synchronous — never calls summarizer
- `append()` graduates when ungraduated > graduate_at
- `append()` evicts when hot > evict_at
- `render()` returns cold summaries + hot contents
- `render()` is synchronous
- `resolve_dirty()` calls summarizer

### Unit Tests (`test_embedding_service.py`)

- `embed()` returns float vector
- Same text produces same embedding
- Different texts produce different embeddings
- Vocabulary grows incrementally
- Cosine similarity: identical texts → 1.0, orthogonal → 0.0

### Unit Tests (`test_cluster_summarizer.py`)

- Calls `model.simple_send_with_retries()` with formatted input
- Falls back to second model on failure
- Raises `ValueError` if all models fail (matches current system contract)

### Integration Tests (`test_chat_summary_uf.py`)

- `ChatSummaryUF` is subclass of `ChatSummary`
- `too_big()` works identically to parent
- `summarize()` returns valid message list
- `summarize()` result ends with assistant message
- `summarize()` output has fewer tokens than input
- `summarize()` falls back to parent if output inflates (budget safety net)
- `summarize_all()` delegates to parent (returns `[summary_msg]`, no ok)
- Incremental feeding: second call only appends new messages
- Stale detection: if messages shrank, forest rebuilds from scratch
- Output is valid `done_messages` format
