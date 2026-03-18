# Transformation Design: Union-Find Context Compaction for Aider

Parameters are starting points from gemini-cli v2. Tuning expected during experiment phase.

## Target Architecture

```
append(msg)       <1ms   Synchronous. TF-IDF embed, push to hot, graduate if overflow.
render()          <1ms   Synchronous. Cached cluster summaries + hot zone verbatim.
resolveDirty()    ~4s    Batch-summarize dirty clusters. Blocks in worker thread.
```

## Integration

```
summarize_start()
  → Thread(target=summarize_worker)
      → ChatSummaryUF.summarize():
          1. Feed messages into context_window.append()
          2. context_window.resolve_dirty()   ← blocking LLM work
          3. context_window.render()           ← uses fresh summaries
          4. Format as aider messages, return
  → User types next message (concurrent)
  → summarize_end()
      → thread.join()
      → if not stale: done_messages = result
```

Existing `summarize_start/worker/end` lifecycle unchanged.

## New Files

### `src/context_window.py`

Port from gemini-cli's `contextWindow.ts`.

**Forest** — dict of clusters, each with summary, centroid, children, dirty inputs.

Key methods:
- `insert(msg_id, content, embedding)` — create singleton
- `union(id_a, id_b)` — synchronous structural merge, marks dirty, no LLM
- `resolve_dirty()` — one summarizer call per dirty root
- `compact(root_id)` — cached summary or raw content
- `nearest_root(embedding)` — closest cluster by cosine similarity

Union collects dirty inputs from both sides (previous summary or raw content), so resolve never re-reads all historical members.

**ContextWindow** — hot zone + cold forest.

- `append(content)` — embed, push to hot, graduate overflow, evict overflow
- `render()` — cold summaries + hot contents
- `resolve_dirty()` — delegates to forest

Graduation: when `len(hot) - graduated_index > 26`, oldest graduates to forest. Merges with nearest cluster if cosine >= 0.15. Force-merges closest pair if cluster count > 10.

Eviction: when `len(hot) > 30`, oldest evicted. Overlap window of ~4 messages gives `resolveDirty()` time to run.

### `src/embedding_service.py`

**TFIDFEmbedder** — incremental TF-IDF, pure Python, no dependencies.

`embed(text)` → sparse float vector. Tokenize (lowercase, split on non-alphanumeric, filter stopwords), compute TF-IDF. Vocabulary grows incrementally.

### `src/cluster_summarizer.py`

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
            except Exception:
                continue
        raise ValueError("cluster summarizer unexpectedly failed for all models")
```

Prompt: summarize fragments, integrate previous summary, preserve file paths / function names / error messages, first-person user voice, no code blocks.

### `src/chat_summary_uf.py`

```python
class ChatSummaryUF(ChatSummary):
    def __init__(self, models=None, max_tokens=1024):
        super().__init__(models, max_tokens)
        self._fed_count = 0
        self._init_context_window()

    def _init_context_window(self):
        self.context_window = ContextWindow(
            embedder=TFIDFEmbedder(),
            summarizer=ClusterSummarizer(self.models),
            graduate_at=26, evict_at=30,
            max_cold_clusters=10, merge_threshold=0.15,
        )
        self._fed_count = 0

    def summarize(self, messages, depth=0):
        if not self.too_big(messages):
            return messages

        # Stale detection: messages shrank → previous result applied → rebuild
        if self._fed_count > len(messages):
            self._init_context_window()

        # Feed only new user/assistant messages
        for msg in messages[self._fed_count:]:
            role = msg.get("role", "").upper()
            if role not in ("USER", "ASSISTANT"):
                continue
            content = msg.get("content", "")
            if content:
                self.context_window.append(f"# {role}\n{content}")
        self._fed_count = len(messages)

        # Resolve dirty clusters, then render fresh summaries
        self.context_window.resolve_dirty()
        rendered = self.context_window.render()

        # Format output
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
            # Not enough messages to form cold clusters (e.g., <27 large messages).
            # Fall back to recursive — don't return unchanged.
            return super().summarize(messages, depth)

        # Budget safety: must fit max_tokens AND be smaller than input
        result_tokens = sum(self.token_count(m) for m in result)
        if result_tokens > self.max_tokens:
            return super().summarize(messages, depth)
        input_tokens = sum(self.token_count(m) for m in messages)
        if result_tokens >= input_tokens:
            return super().summarize(messages, depth)

        if result and result[-1]["role"] != "assistant":
            result.append({"role": "assistant", "content": "Ok."})
        return result

    def summarize_all(self, messages):
        return super().summarize_all(messages)
```

## Construction Site

```python
# args.py
parser.add_argument(
    "--chat-history-summarizer",
    default="recursive",
    choices=["recursive", "union-find"],
)

# main.py
if getattr(args, 'chat_history_summarizer', None) == 'union-find':
    summarizer = ChatSummaryUF(models, max_tokens)
else:
    summarizer = ChatSummary(models, max_tokens)
```

## Tests

**Forest:** insert creates singleton, union is synchronous (no summarizer call), resolve_dirty calls summarizer per dirty root, compact returns cached summary, nearest returns by cosine similarity.

**ContextWindow:** append graduates/evicts at thresholds, render returns cold + hot, resolve_dirty delegates to forest.

**TFIDFEmbedder:** same text → same vector, different texts → different vectors, cosine similarity works.

**ClusterSummarizer:** calls model API, cascades on failure, raises ValueError if all fail.

**ChatSummaryUF:** subclass of ChatSummary, output is valid message list ending with assistant, output fits budget, falls back to recursive on inflation, falls back to recursive when <27 large messages exceed budget (no cold clusters), incremental feeding works, stale detection rebuilds on shrinkage.
