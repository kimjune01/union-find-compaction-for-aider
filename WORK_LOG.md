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

All 5 open questions from systems-comparison resolved:
1. `summarize_all()` → delegates to parent (edit format transitions need single blob)
2. Stale-safety → incremental feeding with stale detection (see discussion below)
3. Output → `[summary_msg, ok_msg, *hot_messages]` matching current structure
4. Voice → first-person user, matching `prompts.summarize` convention
5. Budget → structural bounds + mandatory fallback to recursive

### Step 6: Design Decisions
**File:** `DESIGN_DECISIONS.md`

15 decisions, least to most uncertain:

**Least uncertain (1-3):** Summary voice, model cascade, summarize_all delegation
**Medium (4-9):** Incremental forest, overlap window params, max clusters, merge threshold, TF-IDF choice, cluster prompt
**Most uncertain (10-15):** Output format, no verification pass, no query retrieval, budget enforcement, CLI flag, no persistence

Each decision includes rationale and explicit change trigger.

### Discussion: Gemini-CLI vs Aider Differences

Key differences identified that affect the union-find port:

| Aspect | Gemini-CLI | Aider |
|--------|-----------|-------|
| Algorithm | Flat single-snapshot | Recursive split (stronger baseline) |
| Verification | Two-phase (generate + verify) | Single-phase |
| Tool outputs | Reverse token budget, truncation | None — just strings |
| Threading | None (blocking) | Background thread (existing async plumbing) |
| Failure handling | Token inflation check, previous-failure tracking | ValueError propagation, stale-safety discard |
| Stale-safety | None needed (blocking) | Value equality check, discards if changed |
| `summarize_all()` | Not a separate contract | Separate code path for edit format transitions |

**Three things that matter most:**
1. Stale-safety is the hardest new constraint (gemini-cli never has results discarded)
2. Existing threading is an advantage (resolveDirty can slot into summarize_worker)
3. No tool output handling simplifies the implementation

### Discussion: Stale-Safety Resolution

**Initial approach:** Stateless rebuild each call (~200ms, simple but loses cross-call state).

**Better approach:** Incremental feeding with stale detection.
- If `_fed_count > len(messages)`: previous result was applied (messages shrank) → rebuild
- If `_fed_count <= len(messages)`: previous result was discarded (messages grew) → feed delta
- Forest persists across discarded calls, accumulating topic structure
- Only rebuilds when the underlying messages have been replaced

**Why stale-safety exists:** Prevents data loss. If summarization runs while user adds new messages, applying the result would replace `done_messages` with a compressed version that doesn't include the new messages. The equality check is the simplest correct solution — discard and retry next turn.

### Discussion: Data Structure Choice

Considered whether union-find is over-engineered at k=10 clusters. A plain dict of clusters achieves the same operations:
- Add: scan 10 centroids, pick nearest. O(k).
- Merge: combine two entries. O(1).
- Closest pair: 45 comparisons. Negligible.
- Render: iterate values. O(k).

Decision: keep union-find for now (matches gemini-cli port), but could simplify to dict later. The data structure is not the value — the clustering approach is.

### Discussion: Honest Value Proposition

With stateless rebuild abandoned for incremental persistence, the value proposition is:
1. **Better summaries** — per-cluster (topic-coherent) vs per-token-boundary (arbitrary splits)
2. **Lower cost** — many small LLM calls vs fewer large ones
3. **Non-blocking potential** — though in aider's threading model, resolveDirty still blocks the worker thread

What does NOT survive into done_messages: provenance, expandability, searchability. These exist on the summarizer object but not in the output. Could add commands like `/expand` later.

**Key insight:** The incremental feeding with stale detection (decision #4) preserves cross-call clustering while handling aider's stale-safety model cleanly.

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

### Codex Review #2 + Fixes

**Reviewer:** GPT-5.4 via codex (second pass after first 7 fixes)

**5 issues identified, all fixed:**

1. **Budget check inadequate (HIGH)** — FIXED. The fallback only triggered when result was larger than input, not when it exceeded `max_tokens`. A 20k→12k result with an 8k budget would pass through. Added two-check logic: (a) `result_tokens > self.max_tokens` catches budget violations; (b) `result_tokens >= input_tokens` catches inflation. Updated prose in transformation-design and DESIGN_DECISIONS to match.

2. **Drops message roles, absorbs system messages (HIGH)** — FIXED in prior edit. Role filtering (`if role not in ("USER", "ASSISTANT"): continue`) skips system messages. Content is prefixed with `# ROLE\n` to preserve role structure in cluster summaries, matching current system's `# USER\n{content}\n# ASSISTANT\n{content}` format.

3. **Renders before resolving dirty clusters (HIGH)** — FIXED. Swapped order: `resolve_dirty()` now runs before `render()`. This ensures dirty clusters have fresh summaries when rendered. Updated both the code spec and the integration diagram.

4. **"Never blocks" claim overstated (MEDIUM)** — FIXED. Replaced "Never blocks" with "Blocks at summarize_end() join (same as recursive, but fewer/smaller LLM calls)" in structural differences table. Updated trade-offs table and optimizes-for list. The honest claim is shorter blocking, not zero blocking.

5. **Stale detection gap on equal-length replacement (MEDIUM)** — ACKNOWLEDGED with mitigation analysis. `_fed_count > len(messages)` only detects shrinkage. Equal-length replacement is narrow: aider only modifies `done_messages` by appending or replacing with summary. `summarize_all()` during `Coder.create()` typically reconstructs the summarizer. Outer `summarize_end()` stale check provides a safety net. Content hash comparison documented as the fix if this proves insufficient.

### Simplification

Cut design docs from 5,195 → 1,408 words (73%):
- DESIGN_DECISIONS: 15 decisions → 3 real ones + defaults table. 12 were "do the obvious thing."
- systems-comparison: cut narrative sections that restated the tables
- transformation-design: cut prose that restated the code

### Codex Review #3 + Fixes

**Reviewer:** GPT-5.4 via codex (third pass, post-simplification)

**5 issues identified:**

1. **Compression hole (HIGH)** — FIXED. <27 large messages can exceed `max_tokens` but nothing graduates to cold forest. The `else` branch returned `messages` unchanged. Changed to `return super().summarize(messages, depth)` — falls back to recursive instead of doing nothing.

2. **hot_count vs system messages (MEDIUM)** — NOTED. `hot_count` comes from context window (user/assistant only) but `messages[-hot_count:]` slices from raw list. In practice `done_messages` contains only user/assistant messages (populated from conversation turns). Documented assumption.

3. **Document drift (MEDIUM)** — FIXED. "No changes to stale-safety" → "No changes to the external threading contract." Clarified "~10 summaries" means ~10 cluster summaries joined into 1 output message. "Semantic similarity" → "lexical similarity (TF-IDF)."

4. **Unsupported claims (MEDIUM)** — FIXED. Added "(not validated on aider)" to 0.79x token claim.

5. **Missing impl details (MEDIUM)** — DEFERRED to TDD phase. Render ordering, failure behavior, exact prompt text, thread safety — these belong in the code, not the spec.

### Phase 2 Complete

**Working directory contents:**
```
current-system-extraction.md  — Code extraction
current-system-prose.md       — Prose + constraints
current-system-verification.md — Verification audit
systems-comparison.md         — Recursive vs union-find
transformation-design.md      — Full Python spec
DESIGN_DECISIONS.md           — 3 decisions + defaults table
WORK_LOG.md                   — This file
```
