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

### Step 2: Write Current System Prose (Phase 1.2)
**File:** `current-system-prose.md`

**Description covers:**
- Problem: unbounded done_messages growth
- Trigger: max_chat_history_tokens threshold, checked after every turn
- Algorithm: split at half-budget boundary, summarize head, recurse up to depth 4
- LLM call: format as # ROLE\ncontent, send with summarize prompt, return as user message
- Model cascade: weak_model first, main_model fallback
- Background threading: summarize_start/worker/end lifecycle
- Summary format: first-person user voice, "I spoke to you previously..."
- Edge cases: short histories, deep recursion, model limits, stale-safety

**Known Problems documented (7):**
1. Blocking on deep recursion (multiple sequential LLM calls)
2. Lossy compression with recency bias (older details dropped)
3. No provenance tracking (can't trace summary to source)
4. Cascading information loss (summary of summary)
5. Split-point agnostic to semantics (topics divided at token boundary)
6. No searchability (opaque text blob)
7. One-way door (originals discarded)

**Key difference from gemini-cli baseline:** Aider's recursive approach is more sophisticated. The half-budget split preserves recent messages verbatim at each level. But the fundamental problems remain: lossy, irreversible, no provenance.

### Step 3: Verify Current System (Phase 1.3)
**File:** `current-system-verification.md`

**Verification approach:** Line-by-line semantic audit of prose claims against code extraction.

**Results:**
- Trigger logic: MATCH
- Token budget: MATCH
- Split algorithm: MATCH
- Recursion depth: MATCH
- LLM call format: MATCH
- Model cascade: MATCH
- Output format: MATCH
- Threading model: MATCH
- Stale-safety: MATCH
- Prompt instructions: MATCH
- Edge cases: MATCH
- Known problems (7/7): VERIFIED

**Deltas found:** 3, all low severity:
1. Chat history restore trigger (same mechanism, different entry point — not mentioned in prose)
2. summarize_end() called from summarize_start() (sequencing detail omitted)
3. move_back_cur_messages() optional message parameter (integration detail)

**Checkpoint: PASSED** — No semantic mismatches in the summarization algorithm.

### Step 3b: Document Interface Constraints (Phase 1.3)
**File:** `current-system-prose.md` — added "Constraints on Any Replacement" section

**Constraints identified from base_coder.py and main.py:**

1. **Interface contract:** 3 methods — `too_big()`, `summarize()`, `summarize_all()`. The third is used in a separate code path (edit format transitions in `Coder.create()`), not normal summarization.

2. **Message format:** Plain dicts `{"role", "content"}`. No parts arrays, no tool objects. Content is always a string.

3. **Threading model:** Runs in `threading.Thread`. Must be thread-safe. Current system snapshots with `list()`.

4. **Model access:** Constructor receives `(models, max_tokens)`. Models provide `simple_send_with_retries()`, `token_count()`, `info`, `name`.

5. **Construction site:** Built in `main.py`, passed via `summarizer=` kwarg. No existing CLI flag for strategy selection — would need `--chat-history-summarizer` added to `args.py`.

6. **Output consumed directly:** `self.done_messages = self.summarized_done_messages`. Output becomes prompt verbatim.

7. **Stale-safety tolerance:** Result may be discarded if `done_messages` changed during summarization. Replacement must tolerate this without corrupting internal state.

8. **Statelessness assumption:** Current system is stateless between calls. A stateful replacement must handle the discard case gracefully.

**Key insight:** The `summarize_all()` method is a separate contract from `summarize()`. It's called during edit format switches, not during normal compression. A replacement must implement both.

---

### Phase 1 Complete

**Working directory contents:**
```
current-system-extraction.md  — Code extraction from aider/history.py + base_coder.py
current-system-prose.md       — Plain-language description with Known Problems
current-system-verification.md — Semantic audit: prose ↔ code equivalence
WORK_LOG.md                   — This file
```

**Phase 1 establishes the baseline.** Ready for human review before Phase 2 (Design).
