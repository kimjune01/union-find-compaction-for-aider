# Aider Current Chat Summarization — Code Extraction

This document extracts the relevant code implementing chat history summarization in aider as of commit `HEAD` on `main`.

## Source Files

- `aider/history.py` (143 lines) — `ChatSummary` class
- `aider/coders/base_coder.py` — integration: `summarize_start`, `summarize_worker`, `summarize_end`
- `aider/prompts.py` — summarization prompt and prefix

## Core Architecture

### ChatSummary Class

**Constructor and configuration:**
```python
class ChatSummary:
    def __init__(self, models=None, max_tokens=1024):
        if not models:
            raise ValueError("At least one model must be provided")
        self.models = models if isinstance(models, list) else [models]
        self.max_tokens = max_tokens
        self.token_count = self.models[0].token_count
```

**Instantiation in base_coder.py:**
```python
self.summarizer = summarizer or ChatSummary(
    [self.main_model.weak_model, self.main_model],
    self.main_model.max_chat_history_tokens,
)
```

- Primary model: `weak_model` (cheaper/faster, tried first)
- Fallback model: `main_model` (tried if weak_model fails)
- Token budget: `max_chat_history_tokens` (model-specific limit)

### Trigger: When Summarization Runs

**Threshold check:**
```python
def too_big(self, messages):
    sized = self.tokenize(messages)
    total = sum(tokens for tokens, _msg in sized)
    return total > self.max_tokens
```

**Trigger point in base_coder.py:**
```python
def summarize_start(self):
    if not self.summarizer.too_big(self.done_messages):
        return
    # ... starts background thread
```

**When it fires:**
- After every turn, `move_back_cur_messages()` moves current turn into `done_messages`, then calls `summarize_start()`
- Also fires on chat history restore from `.aider.chat.history.md`
- Fires when `done_messages` token count exceeds `max_chat_history_tokens`

### Entry Point: summarize()

```python
def summarize(self, messages, depth=0):
    messages = self.summarize_real(messages)
    if messages and messages[-1]["role"] != "assistant":
        messages.append(dict(role="assistant", content="Ok."))
    return messages
```

- Delegates to `summarize_real()`
- Ensures result always ends with an assistant message (for valid chat structure)

### Core Algorithm: summarize_real() — Hierarchical Recursive Summarization

```python
def summarize_real(self, messages, depth=0):
    if not self.models:
        raise ValueError("No models available for summarization")

    sized = self.tokenize(messages)
    total = sum(tokens for tokens, _msg in sized)
    if total <= self.max_tokens and depth == 0:
        return messages

    min_split = 4
    if len(messages) <= min_split or depth > 3:
        return self.summarize_all(messages)

    tail_tokens = 0
    split_index = len(messages)
    half_max_tokens = self.max_tokens // 2

    # Iterate over the messages in reverse order
    for i in range(len(sized) - 1, -1, -1):
        tokens, _msg = sized[i]
        if tail_tokens + tokens < half_max_tokens:
            tail_tokens += tokens
            split_index = i
        else:
            break

    # Ensure the head ends with an assistant message
    while messages[split_index - 1]["role"] != "assistant" and split_index > 1:
        split_index -= 1

    if split_index <= min_split:
        return self.summarize_all(messages)

    # Split head and tail
    tail = messages[split_index:]

    # Only size the head once
    sized_head = sized[:split_index]

    # Precompute token limit (fallback to 4096 if undefined)
    model_max_input_tokens = self.models[0].info.get("max_input_tokens") or 4096
    model_max_input_tokens -= 512  # reserve buffer for safety

    keep = []
    total = 0

    # Iterate in original order, summing tokens until limit
    for tokens, msg in sized_head:
        total += tokens
        if total > model_max_input_tokens:
            break
        keep.append(msg)

    summary = self.summarize_all(keep)

    # If the combined summary and tail still fits, return directly
    summary_tokens = self.token_count(summary)
    tail_tokens = sum(tokens for tokens, _ in sized[split_index:])
    if summary_tokens + tail_tokens < self.max_tokens:
        return summary + tail

    # Otherwise recurse with increased depth
    return self.summarize_real(summary + tail, depth + 1)
```

**Algorithm steps:**
1. If total tokens ≤ budget and this is depth 0, return messages unchanged
2. If ≤ 4 messages or depth > 3, summarize everything (base case)
3. Find split point: walk backward from end, accumulating tokens until half the budget
4. Adjust split to land on an assistant message boundary
5. If split point ≤ 4 messages, summarize everything (can't split meaningfully)
6. Truncate head to model's input limit (with 512-token safety buffer)
7. Summarize the head via LLM
8. If summary + tail fits in budget, return
9. Otherwise recurse with depth + 1 (re-split the combined summary + tail)

**Maximum recursion depth: 4** (depth > 3 triggers base case)

### LLM Summarization: summarize_all()

```python
def summarize_all(self, messages):
    content = ""
    for msg in messages:
        role = msg["role"].upper()
        if role not in ("USER", "ASSISTANT"):
            continue
        content += f"# {role}\n"
        content += msg["content"]
        if not content.endswith("\n"):
            content += "\n"

    summarize_messages = [
        dict(role="system", content=prompts.summarize),
        dict(role="user", content=content),
    ]

    for model in self.models:
        try:
            summary = model.simple_send_with_retries(summarize_messages)
            if summary is not None:
                summary = prompts.summary_prefix + summary
                return [dict(role="user", content=summary)]
        except Exception as e:
            print(f"Summarization failed for model {model.name}: {str(e)}")

    raise ValueError("summarizer unexpectedly failed for all models")
```

**Steps:**
1. Concatenate all user and assistant messages into `# ROLE\ncontent` format
2. Skip non-user/assistant roles (system messages excluded)
3. Send to LLM with summarize prompt as system message
4. Try weak_model first, fall back to main_model
5. Prepend `summary_prefix` to LLM output
6. Return as a single user message: `[{"role": "user", "content": "I spoke to you previously..."}]`
7. If all models fail, raise ValueError

### Summarization Prompt

```python
# aider/prompts.py
summarize = """*Briefly* summarize this partial conversation about programming.
Include less detail about older parts and more detail about the most recent messages.
Start a new paragraph every time the topic changes!

This is only part of a longer conversation so *DO NOT* conclude the summary with language like "Finally, ...". Because the conversation continues after the summary.
The summary *MUST* include the function names, libraries, packages that are being discussed.
The summary *MUST* include the filenames that are being referenced by the assistant inside the ```...``` fenced code blocks!
The summaries *MUST NOT* include ```...``` fenced code blocks!

Phrase the summary with the USER in first person, telling the ASSISTANT about the conversation.
Write *as* the user.
The user should refer to the assistant as *you*.
Start the summary with "I asked you...".
"""

summary_prefix = "I spoke to you previously about a number of things.\n"
```

**Key characteristics of the prompt:**
- First-person user voice ("I asked you...")
- Recency bias built into instructions ("less detail about older parts")
- Technical detail preservation ("function names, libraries, packages, filenames")
- No code blocks in output
- Acknowledges partial conversation ("DO NOT conclude")

### Threading: Background Summarization

**Start (called after every turn):**
```python
def summarize_start(self):
    if not self.summarizer.too_big(self.done_messages):
        return
    self.summarize_end()  # join any previous thread first
    if self.verbose:
        self.io.tool_output("Starting to summarize chat history.")
    self.summarizer_thread = threading.Thread(target=self.summarize_worker)
    self.summarizer_thread.start()
```

**Worker (runs in background thread):**
```python
def summarize_worker(self):
    self.summarizing_messages = list(self.done_messages)  # snapshot
    try:
        self.summarized_done_messages = self.summarizer.summarize(self.summarizing_messages)
    except ValueError as err:
        self.io.tool_warning(err.args[0])
    if self.verbose:
        self.io.tool_output("Finished summarizing chat history.")
```

**End (called before sending messages to LLM):**
```python
def summarize_end(self):
    if self.summarizer_thread is None:
        return
    self.summarizer_thread.join()
    self.summarizer_thread = None
    if self.summarizing_messages == self.done_messages:
        self.done_messages = self.summarized_done_messages
    self.summarizing_messages = None
    self.summarized_done_messages = []
```

**Lifecycle:**
1. `move_back_cur_messages()` → `summarize_start()` kicks off background thread
2. While thread runs, user types next message (concurrent)
3. Before LLM call, `summarize_end()` joins thread, swaps in summarized messages
4. **Stale check:** Only applies summary if `done_messages` hasn't changed since thread started (identity check with `==`)

**Where summarize_end is called:**
- `format_messages()` (line 1278) — right before assembling the prompt to send to LLM
- `summarize_start()` (line 1006) — joins previous thread before starting new one

### Message Flow

```
User message arrives
  → Coder processes response (edits, commands, etc.)
  → move_back_cur_messages():
      done_messages += cur_messages
      summarize_start()         # kicks off background thread if too big
      cur_messages = []
  → User types next message (summarizer runs concurrently)
  → format_messages():
      summarize_end()           # blocks: joins thread, applies result
      chunks.done = done_messages  # now summarized
      # assemble full prompt: system + done + repo + files + cur
  → Send to LLM
```

### Token Counting

```python
def tokenize(self, messages):
    sized = []
    for msg in messages:
        tokens = self.token_count(msg)
        sized.append((tokens, msg))
    return sized
```

- Uses `model.token_count(msg)` — model-specific tokenizer
- Counts entire message dict (role + content), not just content string
- Called once per summarization cycle, results reused for split calculation

## Key Characteristics

1. **Hierarchical recursive**: Splits into head + tail, summarizes head, recurses if still too big
2. **Depth-limited**: Maximum 4 levels of recursion before forcing full summarization
3. **Half-budget split**: Tail gets roughly half the token budget, head gets summarized
4. **Model-cascade**: Tries weak_model first, falls back to main_model
5. **Background threaded**: Summarization runs concurrently while user types
6. **Stale-safe**: Only applies result if done_messages unchanged during summarization
7. **User-voiced**: Summary is written as if the user is reminding the assistant
8. **No tool output handling**: Messages are just `{role, content}` dicts, no special truncation
9. **No verification pass**: Single LLM call per summarize_all(), no refinement step
10. **Lossy and irreversible**: Original messages replaced by summary, cannot be recovered
