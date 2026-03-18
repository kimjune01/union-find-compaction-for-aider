# Aider Current Chat Summarization — Verification

This document verifies semantic equivalence between:
- **Code extraction** (`current-system-extraction.md`) — code snippets with annotations
- **Prose description** (`current-system-prose.md`) — plain-language description

## Verification Approach

Line-by-line audit. For each claim in the prose, verify it against the code extraction. Flag any semantic deltas (claims not supported by code, or code behavior not captured in prose).

## Audit Results

### 1. Trigger Logic

**Prose says:** "After every turn, the current exchange moves from `cur_messages` to `done_messages`. The system then checks whether `done_messages` exceeds the token budget."

**Code confirms:**
- `move_back_cur_messages()`: `self.done_messages += self.cur_messages` → `self.summarize_start()`
- `summarize_start()`: checks `self.summarizer.too_big(self.done_messages)`
- `too_big()`: sums token counts, compares to `self.max_tokens`

**Verdict:** MATCH

### 2. Token Budget

**Prose says:** "The threshold is the model's `max_chat_history_tokens`"

**Code confirms:**
- Constructor: `ChatSummary([self.main_model.weak_model, self.main_model], self.main_model.max_chat_history_tokens)`
- `self.max_tokens = max_tokens` (set from `max_chat_history_tokens`)

**Verdict:** MATCH

### 3. Split and Recurse Algorithm

**Prose says:** "Tail — the most recent messages that fit within half the token budget. Head — everything before the tail."

**Code confirms:**
- `half_max_tokens = self.max_tokens // 2`
- Reverse iteration: accumulates tokens until `tail_tokens + tokens < half_max_tokens`
- `tail = messages[split_index:]`, head = `sized[:split_index]`

**Verdict:** MATCH

**Prose says:** "The split point is adjusted to fall on an assistant message boundary"

**Code confirms:**
- `while messages[split_index - 1]["role"] != "assistant" and split_index > 1: split_index -= 1`

**Verdict:** MATCH

**Prose says:** "continues up to depth 4, after which it summarizes everything as a single block"

**Code confirms:**
- `if len(messages) <= min_split or depth > 3: return self.summarize_all(messages)`
- `return self.summarize_real(summary + tail, depth + 1)`

**Verdict:** MATCH (depth > 3 = max 4 levels, since depth starts at 0)

### 4. LLM Call Format

**Prose says:** "Formats all user and assistant messages as `# USER\n{content}\n# ASSISTANT\n{content}`"

**Code confirms:**
- `for msg in messages: role = msg["role"].upper()` → `content += f"# {role}\n"` → `content += msg["content"]`

**Verdict:** MATCH

**Prose says:** "System messages are excluded"

**Code confirms:**
- `if role not in ("USER", "ASSISTANT"): continue`

**Verdict:** MATCH

### 5. Model Cascade

**Prose says:** "tries the `weak_model` first and falls back to the `main_model`"

**Code confirms:**
- Constructor: `[self.main_model.weak_model, self.main_model]`
- `summarize_all()`: `for model in self.models: try: summary = model.simple_send_with_retries(summarize_messages)`

**Verdict:** MATCH

### 6. Summary Output Format

**Prose says:** "The result becomes a single user message prefixed with 'I spoke to you previously about a number of things.'"

**Code confirms:**
- `summary = prompts.summary_prefix + summary` where `summary_prefix = "I spoke to you previously about a number of things.\n"`
- `return [dict(role="user", content=summary)]`
- `summarize()` wrapper: `messages.append(dict(role="assistant", content="Ok."))` if last message isn't assistant

**Verdict:** MATCH

### 7. Background Threading

**Prose says:** "summarize_start() snapshots done_messages and launches a thread"

**Code confirms:**
- `self.summarizer_thread = threading.Thread(target=self.summarize_worker)`
- `self.summarizer_thread.start()`
- Worker: `self.summarizing_messages = list(self.done_messages)` — list copy = snapshot

**Verdict:** MATCH

**Prose says:** "A stale-safety check ensures the result is only applied if done_messages hasn't been modified since the snapshot"

**Code confirms:**
- `summarize_end()`: `if self.summarizing_messages == self.done_messages: self.done_messages = self.summarized_done_messages`

**Verdict:** MATCH. Note: this is an `==` value comparison, not `is` identity check. If `done_messages` has the same content but is a different list object, the check still passes. This is correct behavior (only applies if no new messages were added).

### 8. Summarization Prompt

**Prose says:** "Include function names, libraries, packages, and filenames... more detail about recent messages, less about older ones"

**Code confirms (prompts.py):**
- "The summary *MUST* include the function names, libraries, packages that are being discussed."
- "The summary *MUST* include the filenames that are being referenced"
- "Include less detail about older parts and more detail about the most recent messages."

**Verdict:** MATCH

**Prose says:** "Write as the user addressing the assistant"

**Code confirms:**
- "Phrase the summary with the USER in first person, telling the ASSISTANT about the conversation."
- "Start the summary with 'I asked you...'."

**Verdict:** MATCH

### 9. Edge Cases

**Prose says:** "Very short histories (≤4 messages): Summarized as a single block"

**Code confirms:**
- `min_split = 4` → `if len(messages) <= min_split or depth > 3: return self.summarize_all(messages)`

**Verdict:** MATCH

**Prose says:** "Head is truncated to `max_input_tokens - 512`"

**Code confirms:**
- `model_max_input_tokens = self.models[0].info.get("max_input_tokens") or 4096`
- `model_max_input_tokens -= 512`
- Iterates head, accumulates tokens, breaks when exceeding limit

**Verdict:** MATCH

**Prose says:** "Both models fail: Raises ValueError"

**Code confirms:**
- `raise ValueError("summarizer unexpectedly failed for all models")`
- Caught in worker: `except ValueError as err: self.io.tool_warning(err.args[0])`

**Verdict:** MATCH

### 10. Known Problems

**Problem 1 (blocking on deep recursion):**
Verified. Each `summarize_all()` call is a blocking LLM request. With depth 4, that's up to 4 sequential calls, all blocking the `summarize_worker` thread, which `summarize_end()` joins before the next LLM call.

**Problem 2 (lossy compression with recency bias):**
Verified. Prompt: "Include less detail about older parts." The recursive structure compounds this — older messages get summarized first, then that summary may be re-summarized.

**Problem 3 (no provenance):**
Verified. `summarize_all()` returns `[dict(role="user", content=summary)]`. No metadata linking summary sentences to source messages.

**Problem 4 (cascading information loss):**
Verified. `summarize_real()` recurses with `summary + tail`, feeding prior summaries back into the summarizer. Each level sees a more compressed version of earlier history.

**Problem 5 (semantic-unaware split point):**
Verified. Split is purely token-based: `if tail_tokens + tokens < half_max_tokens`. No semantic boundary detection.

**Problem 6 (no searchability):**
Verified. Output is natural language text. No structured format, tags, or indices.

**Problem 7 (irreversible):**
Verified. `summarize_end()`: `self.done_messages = self.summarized_done_messages`. Original list replaced. No backup stored.

## Semantic Deltas

### Delta 1: Chat history restore trigger

**Code shows (extraction):** `if not self.done_messages and restore_chat_history:` → reads `.aider.chat.history.md` → `self.summarize_start()`

**Prose says:** "Also fires on chat history restore" — Wait, prose doesn't mention this.

**Fix needed:** The prose mentions triggering "after every turn" but doesn't mention the chat history restore path. This is a minor omission — the restore path uses the same `summarize_start()` mechanism.

**Severity:** Low. Same mechanism, different entry point.

### Delta 2: summarize_end() called from summarize_start()

**Code shows:** `summarize_start()` calls `self.summarize_end()` first (joins any previous thread before starting new one).

**Prose says:** "summarize_end() joins the thread and applies the result" but doesn't explicitly say it's called at the start of summarize_start().

**Severity:** Low. The prose captures the lifecycle correctly (start → work → end), but doesn't detail the "end previous before starting new" sequencing.

### Delta 3: move_back_cur_messages() also appends user message

**Code shows:**
```python
def move_back_cur_messages(self, message):
    self.done_messages += self.cur_messages
    self.summarize_start()
    if message:
        self.done_messages += [
            dict(role="user", content=message),
            dict(role="assistant", content="Ok."),
        ]
    self.cur_messages = []
```

**Prose doesn't mention:** The optional `message` parameter that appends an extra user+assistant pair after starting summarization.

**Severity:** Low. This is an integration detail about how the coder manages message flow, not about the summarization algorithm itself.

## Verification Summary

| Aspect | Verdict |
|--------|---------|
| Trigger logic | MATCH |
| Token budget | MATCH |
| Split algorithm | MATCH |
| Recursion depth | MATCH |
| LLM call format | MATCH |
| Model cascade | MATCH |
| Output format | MATCH |
| Threading model | MATCH |
| Stale-safety | MATCH |
| Prompt instructions | MATCH |
| Edge cases | MATCH |
| Known problems (7/7) | VERIFIED |

**Deltas found:** 3, all low severity (integration details, not algorithmic)

**Checkpoint: PASSED** — Prose accurately describes the code. No semantic mismatches in the summarization algorithm or its observable behavior.
