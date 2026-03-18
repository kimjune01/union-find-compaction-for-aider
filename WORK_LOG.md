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

---

## Phase 2: Design

### Step 4: Systems Comparison
**File:** `systems-comparison.md`

Compared aider's hierarchical recursive summarization vs union-find structured compaction across:
- Architecture (recursive split vs forest clusters)
- Recency handling (degrades with depth vs fixed hot zone)
- Topic handling (token-boundary splits vs semantic clustering)
- Cost model (1-4 large calls vs many small calls)
- Failure modes (cascading loss vs retrieval misses)
- Trade-offs table

5 open questions for integration identified:
1. How does union-find interact with `summarize_all()`?
2. How does stale-safety work with stateful forest?
3. Output format as aider messages?
4. Cluster summary voice convention?
5. Token budget compliance?

### Step 5: Transformation Design
**File:** `transformation-design.md`

Full Python spec for 4 new modules:
1. `context_window.py` — Forest + ContextWindow (port from gemini-cli TS)
2. `embedding_service.py` — TFIDFEmbedder (pure Python, no deps)
3. `cluster_summarizer.py` — wraps aider's model.simple_send_with_retries()
4. `chat_summary_uf.py` — ChatSummaryUF(ChatSummary) drop-in subclass

**Critical design resolution: stateless per call.**
The stale-safety model forced a decision: rebuild the forest from scratch each `summarize()` call instead of persisting across calls. If `summarize_end()` discards the result, a persistent forest would diverge from `done_messages`. Rebuilding avoids this at ~200ms cost (background thread, invisible to user).

All 5 open questions from systems-comparison resolved:
1. `summarize_all()` → delegates to parent (edit format transitions need single blob)
2. Stale-safety → stateless rebuild avoids the problem entirely
3. Output → `[summary_msg, ok_msg, *hot_messages]` matching current structure
4. Voice → first-person user, matching `prompts.summarize` convention
5. Budget → structural bounds (10 clusters × 200 tokens + 30 messages × 200 tokens ≈ 8,000)

### Step 6: Design Decisions
**File:** `DESIGN_DECISIONS.md`

15 decisions, least to most uncertain:

**Least uncertain (1-3):** Summary voice, model cascade, summarize_all delegation
**Medium (4-9):** Stateless rebuild, overlap window params, max clusters, merge threshold, TF-IDF choice, cluster prompt
**Most uncertain (10-15):** Output format, no verification pass, no query retrieval, budget enforcement, CLI flag, no persistence

Each decision includes rationale and explicit change trigger.

**Key insight from this phase:** The incremental feeding with stale detection (decision #4) preserves cross-call clustering while handling aider's stale-safety model cleanly.

### Codex Review + Fixes

**Reviewer:** GPT-5.4 via codex (skeptical methodologist)

**7 issues identified, all fixed:**

1. **Internal inconsistency (stateless vs persistent)** — FIXED. Replaced stateless rebuild with incremental feeding + stale detection. Forest persists across calls when result is discarded; rebuilds only when result is applied (messages shrank). Architecture is honestly persistent now.

2. **Output contract misstated** — FIXED. Clarified that `summarize_all()` returns `[summary_msg]` (one item), while `summarize()` wrapper appends assistant "Ok." message. Corrected in systems-comparison and transformation-design.

3. **Budget compliance soft** — FIXED. Added mandatory post-render token check in `ChatSummaryUF.summarize()`. If output tokens >= input tokens, falls back to `super().summarize()` (recursive). Union-find never produces a worse result than current system.

4. **TF-IDF embedding drift** — FIXED. Explained why drift is manageable: forest rebuilds on result application (stale detection resets embeddings), centroids recomputed on merge, and k=10 clusters with 0.15 threshold is robust to small IDF changes.

5. **Gemini-cli evidence doesn't transfer** — FIXED. Labeled all parameters as "starting points from gemini-cli, not validated on aider." Aider-specific tuning expected during experiment phase.

6. **Silent fallback to concatenation** — FIXED. `ClusterSummarizer` now raises `ValueError` when all models fail, matching current system's contract.

7. **Version drift between docs** — FIXED. Replaced "Open Questions" in systems-comparison with "Integration Decisions (Resolved)" linking to transformation-design and DESIGN_DECISIONS. Removed "retrieval miss" failure mode (design renders all clusters, no query-based retrieval).

### Phase 2 Complete

**Working directory contents:**
```
current-system-extraction.md  — Code extraction
current-system-prose.md       — Prose + constraints
current-system-verification.md — Verification audit
systems-comparison.md         — Recursive vs union-find
transformation-design.md      — Full Python spec
DESIGN_DECISIONS.md           — 15 decisions with rationale
WORK_LOG.md                   — This file
```
