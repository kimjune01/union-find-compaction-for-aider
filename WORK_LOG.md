# Work Log: Union-Find Compaction for Aider

## 2026-03-18

### Step 1: Extract Current System (Phase 1.1)
**File:** `current-system-extraction.md`

**Source files analyzed:**
- `aider/history.py` (143 lines) — ChatSummary class
- `aider/coders/base_coder.py` — integration: summarize_start/worker/end threading
- `aider/prompts.py` — summarization prompt and summary_prefix

**Key findings:**

**Architecture:** Hierarchical recursive summarization (NOT flat like gemini-cli)
- Split messages into head + tail at ~50% token budget boundary
- Summarize head via LLM
- If summary + tail still too big, recurse (max depth 4)
- Base case: ≤4 messages or depth >3 → summarize everything

**Integration:**
- Background threaded: `summarize_start()` kicks off `threading.Thread`
- Runs concurrently while user types next message
- `summarize_end()` joins thread before next LLM call
- Stale-safety: only applies if `done_messages` unchanged during summarization

**Model cascade:**
- Tries `weak_model` first (cheaper/faster)
- Falls back to `main_model` if weak_model fails
- Token budget: `max_chat_history_tokens`

**Summary format:**
- Single user message: `{"role": "user", "content": "I spoke to you previously about a number of things.\n..."}`
- Written in first person user voice ("I asked you...")
- Followed by `{"role": "assistant", "content": "Ok."}`

**Prompt instructs:**
- Less detail about older parts, more about recent
- Must include function names, libraries, packages, filenames
- No code blocks in output
- Don't conclude (partial conversation)

**What this is NOT (vs gemini-cli):**
- NOT flat (recursive split)
- NOT two-phase (no verification pass)
- NOT tool-output-aware (just strings)
- NOT model-routed to compression-specific models (uses weak_model)

This is a stronger baseline than gemini-cli's flat compression. The recursive splitting preserves more structure.
