# Aider Current Chat Summarization — Prose Description

## Problem

Aider maintains two message lists: `done_messages` (completed turns) and `cur_messages` (current turn). As conversations grow, `done_messages` accumulates tokens beyond what the LLM can accept. The summarizer compresses `done_messages` to stay within `max_chat_history_tokens`.

## When Summarization Triggers

After every turn, the current exchange moves from `cur_messages` to `done_messages`. The system then checks whether `done_messages` exceeds the token budget. If it does, summarization starts immediately in a background thread.

The threshold is the model's `max_chat_history_tokens` — the portion of the context window allocated to chat history (after accounting for system prompt, repo map, file contents, and current turn).

## How Summarization Works

### Split and Recurse

The algorithm splits messages into two groups:

1. **Tail** — the most recent messages that fit within half the token budget. These are kept verbatim.
2. **Head** — everything before the tail. This gets summarized by the LLM.

The split point is adjusted to fall on an assistant message boundary, ensuring conversation turns aren't broken mid-exchange.

If head + tail still exceeds the budget after summarization, the algorithm recurses: it treats the combined (summary + tail) as a new message list and splits again. This continues up to depth 4, after which it summarizes everything as a single block.

### The LLM Call

For each summarization chunk, the system:

1. Formats all user and assistant messages as `# USER\n{content}\n# ASSISTANT\n{content}`
2. Sends this to the LLM with a system prompt requesting a brief summary
3. The summary is written in first-person user voice: "I asked you..."
4. The result becomes a single user message prefixed with "I spoke to you previously about a number of things."

System messages are excluded from the formatted input.

### Model Selection

The system tries the `weak_model` first (typically a cheaper, faster model) and falls back to the `main_model` if the weak model fails. This keeps summarization costs low for the common case.

### Background Threading

Summarization runs in a `threading.Thread` while the user types their next message:

1. After a turn completes, `summarize_start()` snapshots `done_messages` and launches a thread
2. The thread runs `summarize()` on the snapshot
3. Before the next LLM call, `summarize_end()` joins the thread and applies the result
4. A stale-safety check ensures the result is only applied if `done_messages` hasn't been modified since the snapshot was taken

This means summarization overlaps with user think time. The user only blocks on summarization if they type faster than the summarizer can finish — which happens when the summarizer must make multiple recursive LLM calls on a large history.

### What the Summary Contains

The prompt instructs the LLM to:
- Include function names, libraries, packages, and filenames
- Provide more detail about recent messages, less about older ones
- Start new paragraphs when topics change
- Not include code blocks
- Not conclude the summary (it's a partial conversation)
- Write as the user addressing the assistant

### What the Summary Replaces

After summarization, `done_messages` contains:
```
[{"role": "user", "content": "I spoke to you previously about a number of things.\n...summary..."}]
[{"role": "assistant", "content": "Ok."}]
```

The original messages are discarded. The summary is the only record of the compressed history within the conversation context.

## Edge Cases

- **Very short histories** (≤4 messages): Summarized as a single block, no splitting
- **Deep recursion** (depth > 3): Falls through to summarize everything, prevents infinite recursion
- **Model input limit exceeded**: Head is truncated to `max_input_tokens - 512` before sending to LLM
- **Both models fail**: Raises `ValueError`, surfaced as a warning in the UI
- **Messages modified during summarization**: Result is discarded (stale-safety check)

## Known Problems

1. **Blocking on deep recursion.** When history is large enough to require multiple recursive passes, each pass makes an LLM call. The user blocks at `summarize_end()` until all passes complete. With depth 4, this could mean 4 sequential LLM calls.

2. **Lossy compression with recency bias.** The prompt explicitly instructs "less detail about older parts." Information mentioned early in a long conversation — file paths, configuration values, error messages — may be reduced to a sentence or dropped entirely. Users must re-state these details.

3. **No provenance tracking.** The summary is a single text blob. There is no way to trace which part of the summary came from which original message. If the summary contains an error, there is no way to check it against the source.

4. **Cascading information loss.** Each recursive level summarizes a summary. The head of the first split gets summarized, then that summary may get re-summarized in the next recursive pass. Details that survive the first compression may be lost in the second.

5. **Summary quality depends on split point.** The split point is determined by token count, not semantic boundaries. A topic that spans the split boundary gets divided: half goes to the summary, half stays verbatim. This can produce summaries that lack context for the topics they describe.

6. **No searchability.** The summary is opaque natural language. Users cannot search their compressed history for specific terms, file names, or decisions. They must read the entire summary to find relevant information.

7. **One-way door.** Original messages are replaced. There is no undo, no expansion, no way to recover the original conversation after compression.
