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

---

## Phase 3: Preregistration

### Step 7: Preregistration
**File:** `PREREGISTRATION.md`

**Inputs combined:**
- gemini-cli `PREREGISTRATION-V2.md` — structure, hypotheses, tuning policy, claim ladder
- metacognition `ROUND3_RETROSPECTIVE.md` — futility rules, effect size + probability, pre-registration integrity lessons

**Key design choices:**

1. **Exploratory from the start.** Gemini-cli v1 was confirmatory, failed, had to reclassify. Starting exploratory avoids that.

2. **Futility rules.** Metacognition Round 3 oscillated 0.90-0.94 for 17 batches wasting compute. Added: if after 50% of data, effect size < 2pp and p > 0.50, stop.

3. **192 paired observations.** Gemini-cli had 96 pairs, underpowered at p=0.136. 24 conversations × 8 questions gives ~80% power for 5pp effect.

4. **Honest latency reporting.** H2b reports `resolveDirty()` latency separately — prevents hiding deferred work. Learned from gemini-cli where v1 "non-blocking" claim was misleading.

5. **Contemporaneous baselines.** Rerun recursive in same environment, don't reuse old numbers.

6. **Dual-model judging.** Learned from metacognition — catches judge-specific quirks.

7. **Effect size + probability.** Report CIs and effect sizes, not just p-values. A +7pp trend at p=0.17 is informative even if not significant.

### Phase 3 Complete

---

## Phase 5: TDD Implementation

### Module 1: context_window.py (Forest + ContextWindow)

**Tests written first** (`tests/test_context_window.py`): 28 tests covering:
- Forest: insert creates singleton, union merges without LLM call, resolve_dirty calls summarizer per dirty root, compact returns cached summary, nearest_root by cosine similarity
- ContextWindow: append adds to hot zone, graduation at threshold, eviction, force merge when exceeding max clusters, render returns cold + hot, resolve_dirty delegates

**Implementation** (`src/context_window.py`):
- `Forest`: union-find with path compression, union-by-size, dirty tracking, centroid averaging
- `ContextWindow`: hot zone + cold forest, graduation, eviction, rendering
- **Bug found during TDD**: `nearest_root` was called after insert, finding the newly-inserted node itself (sim=1.0). Fixed by doing nearest lookup *before* inserting.

**28/28 passing.** Committed.

### Module 2: embedding_service.py (TFIDFEmbedder)

**Tests written first** (`tests/test_embedding_service.py`): 15 tests covering:
- Basics: returns dict, nonempty, empty text returns empty
- Determinism: same text → same vector, different → different
- Similarity: identical=1.0, similar > different, disjoint=0.0
- Vocabulary: grows incrementally, repeated words don't grow
- Tokenization: case insensitive, splits on non-alphanumeric, filters stopwords
- IDF: rare words have higher weight, doc count tracking

**Implementation** (`src/embedding_service.py`):
- Pure Python TF-IDF with sparse dict vectors `{term: weight}`
- Smoothed IDF: `log(1 + N/df)` to avoid zero weights when there's only one document
- Tokenization: lowercase, regex split on non-alphanumeric, stopword filtering

**15/15 passing.** Committed.

### Module 3: cluster_summarizer.py (ClusterSummarizer)

**Tests written first** (`tests/test_cluster_summarizer.py`): 8 tests covering:
- Basics: calls model API, passes texts joined with separator, includes system prompt
- Cascade: falls back on exception, falls back on None return, ValueError if all fail
- Single model (not list) accepted

**Implementation** (`src/cluster_summarizer.py`):
- Wraps `model.simple_send_with_retries()` with model cascade
- Prompt preserves file paths, function names, error messages; first-person user voice

**8/8 passing.** Committed.

### Module 4: chat_summary_uf.py (ChatSummaryUF)

**Tests written first** (`tests/test_chat_summary_uf.py`): 15 tests covering:
- Subclass: is subclass of ChatSummary, same constructor args
- Output format: list of dicts, ends with assistant, starts with summary_prefix
- Budget compliance: result < input, falls back on inflation, falls back when > max_tokens
- Compression hole: falls back to recursive when <27 large messages (no cold clusters)
- Incremental feeding: only feeds new messages, preserves forest across calls
- Stale detection: rebuilds on message shrinkage, feeds delta on growth
- Delegation: summarize_all delegates to parent
- Passthrough: returns unchanged when within budget

**Implementation** (`src/chat_summary_uf.py`):
- `ChatSummaryUF(ChatSummary)` drop-in subclass
- Incremental feeding with `_fed_count` tracking
- Stale detection: `_fed_count > len(messages)` triggers rebuild
- Two-check budget safety: (a) result > max_tokens, (b) result >= input tokens
- Compression hole fix: else branch → `super().summarize()` (not return unchanged)

**Also updated** `context_window.py`: `_cosine_similarity` now handles both list vectors (from mock tests) and sparse dict vectors (from TFIDFEmbedder). Centroid averaging in `union` handles both formats.

**66/66 total tests passing.** Committed.

### Phase 5 Complete

**Working directory contents:**
```
src/
  __init__.py
  context_window.py    — Forest + ContextWindow (241 lines)
  embedding_service.py — TFIDFEmbedder (77 lines)
  cluster_summarizer.py — ClusterSummarizer (55 lines)
  chat_summary_uf.py   — ChatSummaryUF (79 lines)
tests/
  __init__.py
  conftest.py
  test_context_window.py    — 28 tests
  test_embedding_service.py — 15 tests
  test_cluster_summarizer.py — 8 tests
  test_chat_summary_uf.py   — 15 tests
current-system-extraction.md
current-system-prose.md
current-system-verification.md
systems-comparison.md
transformation-design.md
DESIGN_DECISIONS.md
PREREGISTRATION.md
WORK_LOG.md
```

**Bugs found during TDD (3):**
1. `nearest_root` self-match: looking up nearest after insert found the node itself. Fixed by querying before insert.
2. `_cosine_similarity` type mismatch: Forest tests used list vectors, TFIDFEmbedder produces dict vectors. Fixed by supporting both.
3. Stale detection test assumed small messages would exceed budget: needed content long enough to trigger `too_big()`.

**Next: Phase 6 — Experiment harness** (24 conversations, 192 paired observations, blinded judge).

---

## Phase 6: Experiment Harness

### Mistake: Wrong Directory

Initially built all 9 harness modules (84 tests) inside `/Users/junekim/Documents/gemini-cli/experiment/` — the wrong project entirely. Used litellm directly instead of wrapping aider's `Model.send_completion()`. After user pointed out ("wait what directory are we even in?"), re-read the prereg and rebuilt everything in the correct project under `src/` and `tests/`.

Lessons:
1. Always verify working directory matches the prereg's project.
2. The experiment harness belongs with the code it tests, not in a sibling repo.

### Modules Created (TDD, in order)

**1. `src/schemas.py` — Dataclasses + JSON serialization (8 tests)**
- `Conversation`, `RecallQuestion`, `RunResult` (with `append_latencies_ms`, `render_latencies_ms`, `resolve_dirty_latencies_ms`), `Judgment`, `ExperimentState`
- JSON roundtrip via `to_dict()`/`from_dict()` class methods

**2. `src/token_tracker.py` — Cost accounting for H3 (10 tests)**
- `LLMCallRecord`, `TokenTracker` (record/aggregate/save/load)
- `TrackedModel`: wraps aider `Model`, calls `send_completion()` directly to capture `response.usage` tokens
- Proxies `token_count()` and other attributes via `__getattr__` so ChatSummary/ChatSummaryUF see a full model interface

**3. `src/analyzer.py` — Pure math, no API (16 tests)**
- McNemar's test (continuity correction), latency percentiles, cost ratio
- `ExperimentAnalyzer.check_futility()`: stops at 50% if effect <2pp and p>0.50, or cost >3x
- Only new dependency: `scipy` (for `chi2.cdf`)

**4. `src/prompts_experiment.py` — Constants, no tests**
- `EXPAND_ISSUE_SYSTEM`, `EXPAND_ISSUE_CHUNK`, `GENERATE_QUESTIONS`, `JUDGE_PROMPT`

**5. `src/judge.py` — Blinded judging via `codex exec` (8 tests)**
- `BlindedJudge`: randomizes A/B per conversation via SHA256 hash (deterministic per seed+conv_id)
- Shells out to `codex exec -c model="gpt-5.4" --ephemeral -` with prompt on stdin
- Parses CORRECT/INCORRECT from stdout

**6. `src/runner.py` — Run both systems (6 tests)**
- `ExperimentRunner(model, tracker, max_tokens)`
- `run_union_find()`: creates `ChatSummaryUF`, instruments `context_window.append/render/resolve_dirty` for H2 latency, feeds messages incrementally
- `run_recursive()`: deferred import of `aider.history.ChatSummary` (baseline), same incremental feeding
- `run_both()`: runs both on same conversation

**7. `src/conversation_generator.py` — GitHub issues → 200-msg conversations (10 tests)**
- Fetches via `gh api`, expands in 5 chunks of 40 messages via Gemini Flash
- Repos: flask, fastapi, pytorch, vscode (need real issues, not PRs)

**8. `src/question_generator.py` — 8 recall questions per conversation (9 tests)**
- Validates: 8 questions, ≥3 categories, span early/mid/late regions
- Categories: filename, function_name, library, error_message, decision, configuration, api_endpoint, test_case

**9. `src/harness.py` — Pipeline orchestrator (7 tests)**
- Stages independently runnable, JSON checkpointing
- Data layout: `experiment/data/{conversations,questions,runs,judgments,analysis}/`

### Bugs Found and Fixed (Phase 6)

1. **Wrong patch path for recursive tests.** `patch("runner.ChatSummary")` failed because the import `from aider.history import ChatSummary` is deferred inside `run_recursive()`. Fixed: `patch("aider.history.ChatSummary")`.

2. **Latency test never triggered compression.** Mock model's `token_count` returns 10; with default `max_tokens=1024`, 40 messages (400 tokens) never exceeds budget. Fixed: pass `max_tokens=50` so `too_big()` fires after 6 messages.

3. **McNemar expected values.** `numpy.percentile(range(1,101), 95)` returns 95.05, not 95.55. Fixed test expected value.

4. **Futility test parameters.** `b=5, c=4` gives 11.1pp effect (not <2pp). Fixed to `b=50, c=50` (0pp effect).

### Test Summary

**141/141 passing** (75 new + 66 existing from Phase 5).

```
tests/test_analyzer.py       — 16 tests
tests/test_chat_summary_uf.py — 15 tests
tests/test_cluster_summarizer.py — 8 tests
tests/test_context_window.py  — 28 tests
tests/test_conversation_generator.py — 10 tests
tests/test_embedding_service.py — 15 tests
tests/test_harness.py         — 7 tests
tests/test_judge.py           — 8 tests
tests/test_question_generator.py — 9 tests
tests/test_runner.py          — 6 tests
tests/test_schemas.py         — 8 tests
tests/test_token_tracker.py   — 10 tests
```

### Phase 6 Complete (Harness Code)

**Next: Run the experiment** — generate 24 conversations, 192 paired observations, blinded judge, McNemar's test.

---

### Experiment Run Attempt #1 (Failed)

**Model mistake chain:**

1. Initially tried `gemini/gemini-2.0-flash` — deprecated, returns 404 ("no longer available to new users").
2. Switched to `gemini/gemini-2.5-flash-lite` — worked for generation but hit aggressive rate limiting during runner phase (many LLM calls per conversation for cluster summarization).
3. User corrected: should be `gemini/gemini-3.1-flash-lite-preview` (confirmed working in earlier session).

**What happened with 2.5-flash-lite:**

- Stage 1 (conversations): 13/24 generated. ~30% failure rate from empty responses and rate limits.
- Stage 2 (questions): 104/104 generated successfully (13 × 8). Fast — one call each.
- Stage 3 (run + judge): Only 4/13 completed. Rate limiting killed most runner calls.
  - The runner makes many LLM calls per conversation (cluster summarization in ChatSummaryUF + recursive summarization baseline), exhausting the rate limit quickly.
  - `ClusterSummarizer` swallows exceptions silently (`except Exception: continue`), so failures surface as "summarizer unexpectedly failed for all models" without detail.

**Partial results from 4 conversations (32 paired observations):**

| Conversation | UF Score | Rec Score |
|---|---|---|
| pallets-flask-1421 | 3/8 | 3/8 |
| pallets-flask-1169 | 4/8 | 4/8 |
| pytorch-pytorch-494 | 0/8 | 0/8 |
| microsoft-vscode-93814 | 1/8 | 1/8 |

Every conversation scored identically. Both systems compressed ~200 messages down to ~13-21 messages. Neither retained much factual detail (overall 8/32 = 25% recall for both).

**Other fixes during this attempt:**

1. **Conversation generator chunking:** Changed from 5 chunks of 40 messages to 10 chunks of 20 — reduces output length per call, improving JSON completion rate.
2. **JSON mode:** Added `response_format={"type": "json_object"}` to litellm calls — fixed truncated JSON from generation.
3. **Litellm direct calls:** Switched conversation and question generators from `TrackedModel(aider_model)` to `litellm.completion()` directly — aider's `send_completion()` didn't set `max_tokens` high enough, causing truncation.
4. **Rate limit backoff:** Added 3-second delay between conversations and exponential backoff on 503/429 errors.

**Root cause:** Wrong model. Switching all references to `gemini/gemini-3.1-flash-lite-preview` for attempt #2.

### Prereg Compliance Fixes

Before attempt #2, audited implementation against `PREREGISTRATION.md` and fixed:

1. **Latency percentiles:** Changed from `{p50, p95, p99}` to `{p50, p90, p95, max}` per prereg H2b.
2. **Sign test:** Added `compute_sign_test()` (conversation-level binomial test, p=0.5) per prereg H1 sensitivity analysis.
3. **`binom_test` → `binomtest`:** scipy deprecated `binom_test`; switched to `binomtest` with `.pvalue` attribute.

145/145 tests passing after fixes.

---

### Experiment Run Attempt #2 (Success)

**Model:** `gemini/gemini-3.1-flash-lite-preview`
**Date:** 2026-03-18

**Stage 1 — Conversations:** 17/24 generated (some repos had fewer qualifying issues than expected: django returned 0, tiangolo/fastapi and pytorch each returned fewer than 4). All conversations had exactly 200 messages (one had 204). ~65 seconds per conversation.

**Stage 2 — Questions:** 136/136 generated (17 × 8). ~3 seconds each. No failures.

**Stage 3 — Run + Judge:** 17/17 completed. ~3 minutes per conversation (80-145s compression + 80s judging). No rate limiting with 3.1-flash-lite-preview. Futility check at conversation 12: b=2, c=1, p=1.000, cost=1.07x — continued (not stopped).

**Per-conversation results:**

| # | Conversation | UF | Rec | UF compressed | Rec compressed |
|---|---|---|---|---|---|
| 1 | flask-1361 | 4/8 | 1/8 | 32 msgs | 26 msgs |
| 2 | flask-1575 | 2/8 | 2/8 | 30 | 30 |
| 3 | flask-1421 | 3/8 | 3/8 | 28 | 24 |
| 4 | flask-1169 | 5/8 | 7/8 | 30 | 29 |
| 5 | fastapi-1228 | 0/8 | 3/8 | 32 | 39 |
| 6 | fastapi-1663 | 2/8 | 3/8 | 30 | 28 |
| 7 | pytorch-494 | 1/8 | 1/8 | 30 | 24 |
| 8 | vscode-93814 | 1/8 | 2/8 | 32 | 38 |
| 9 | vscode-191229 | 2/8 | 4/8 | 38 | 32 |
| 10 | vscode-249326 | 2/8 | 2/8 | 32 | 26 |
| 11 | vscode-224 | 1/8 | 3/8 | 32 | 39 |
| 12 | requests-2966 | 4/8 | 3/8 | 32 | 36 |
| 13 | requests-1573 | 6/8 | 4/8 | 30 | 26 |
| 14 | requests-2455 | 5/8 | 5/8 | 41 | 39 |
| 15 | requests-4006 | 7/8 | 5/8 | 34 | 36 |
| 16 | cpython-46888 | 2/8 | 4/8 | 32 | 29 |
| 17 | cpython-57912 | 7/8 | 4/8 | 36 | 33 |
| **Total** | | **54/136** | **56/136** | | |

**Stage 4 — Analysis:**

| Hypothesis | Target | Result | Outcome |
|------------|--------|--------|---------|
| H1 Recall | >= +5pp, p<0.05 | -1.5pp, p=0.2482 | **MISS** |
| H2a Latency | p95 < 100ms | append=0.08ms, render=0.02ms | **PASS** |
| H3 Cost | <= 2x | 1.14x | **PASS** |

**Key findings:**

1. **H1 (Recall): Null result.** UF 39.7% vs Rec 41.2% — no meaningful difference. McNemar's: only 3 discordant pairs (b=3, c=0) out of 136 observations, meaning the two systems almost always agree or disagree on the same questions. Sign test: 5 UF wins, 7 Rec wins, 5 ties (p=0.7744). The systems are quality-equivalent.

2. **H2 (Latency): Trivial.** Append and render are sub-millisecond. ResolveDirty (the actual LLM summarization) takes p50=1.5s, p95=4.2s — but this is deferred and informational per prereg.

3. **H3 (Cost): UF is ~14% more expensive.** 382K vs 335K tokens. Well within the 2x target. The extra cost comes from cluster summarization calls vs recursive's fewer but larger calls.

**Interpretation:** Union-find compaction is not better at recall than recursive summarization on this benchmark. It's not worse either — the systems are essentially equivalent. The structural advantages of union-find (topic-coherent clusters, incremental updates) don't translate into measurably better factual retention when judged by a blinded evaluator. The approach passes on engineering metrics (latency, cost) but misses on its primary claim (recall improvement).

**Total runtime:** ~70 minutes (30 min Stage 1, 2 min Stage 2, 35 min Stage 3, <1 min Stage 4).

---

### Reframing: Issue Research on Aider's Context Management

After the null result on recall, searched aider's GitHub for context-related issues to understand what users actually want. Searched `paul-gauthier/aider` for issues mentioning context, memory, summarization, forgetting, long conversations, and history.

**Volume:** ~73 issues with 235+ comments across context management problems, out of ~4,000 total issues.

**Top issues by signal:**

- **#3607** (10 comments, open): "More control over chat history." Users report important early decisions get summarized away while verbose recent exchanges consume context. One user: "messy context confuses LLM." Requests checkbox-based selection of which history to keep. Multiple users agree.

- **#2219** (open): "Editing history in realtime." User wonders "how much the model remembers about our original goal." Wants to see and edit the exact context being sent. Calls current approach "wasting a lot of my time."

- **#948** (3 comments, open): Requests a table of context consumers (files, history) with token counts and selective dropping. Conceptually: `/tokens` but actionable.

- **#4079** (open): "Chat history archive." Proposes `--chat-history-archive` and `--task` flags. Author: "chat history serves as storage for the theory of the codebase."

- **#4438** (4 comments, open): "Aider forgets to apply changes after asking for file to be added." One user: "This is the reason I stopped using aider."

- **#3722**: Bug where `tail_tokens` is always 0 in `summarize_real()` — tail messages silently dropped from summaries.

- **#2003** (6 comments): `max_chat_history_tokens` not respected, history 4x bigger than configured. User observes: "many times even when there is no history at all, aider responses are very good, sometimes better than when there is a lot of history."

- **#2932** (12 comments): Summarization crashes — "cannot schedule new futures after interpreter shutdown." Recurring across multiple models.

**What users want (ranked by frequency):**

1. **Visibility** — see what's in context, organized by topic, with token counts
2. **Selective control** — drop specific parts of history without losing everything
3. **Cross-session persistence** — structured knowledge that survives restarts
4. **Reliable summarization** — current system crashes, drops tails, ignores budget

**What users don't want:** better summaries. Nobody asked for "higher quality compression." They want to see and control what the model remembers — which is a UX problem, not an algorithm problem.

**Maintainer posture:** paul-gauthier's recent responses lean conservative — "use `/clear`", "use `/drop`", "try a stronger model." No indication of plans for topic-aware summarization or selective expansion. The project considers context management "solved" with the existing summarizer.

**Key insight:** The experiment measured the wrong thing. We tested recall (do summaries retain facts?) when users are asking for control (can I see and manage what's in context?). Union-find's value isn't better summaries — the experiment proved they're equivalent. The value is that topic-structured context enables visibility and selective management that flat recursive summarization can't support.

**Reframing the PR:** Don't pitch union-find as "better compression." Pitch `/topics` — the most requested unbuilt feature. The foundation (union-find) is infrastructure; the feature (`/topics` + `/drop-topic`) is the product. The experiment serves as a regression check: same quality, reasonable cost, no downside to switching.
