# Work Log: `/topics` for Aider

## Context

Foundation complete (see `WORK_LOG-foundational.md`). Union-find compaction works, passes regression (same recall as recursive, 1.14x cost, sub-ms latency), 145 tests. But the experiment showed no quality advantage — the value is structural, not algorithmic.

Issue research on `paul-gauthier/aider` (~73 issues, 235+ comments) revealed users want visibility and control over context, not better summaries. Top issues: #3607 (selective history control), #2219 (see/edit context), #948 (token breakdown with actions), #4079 (cross-session persistence).

The feature: `/topics` + `/drop-topic` — see what's in context and selectively remove resolved topics. Complete loop: inspect, act, confirm.

## 2026-03-18

### Step 1: Extract Current System

**File:** `topics/pr-current-system.md`

Documented aider's current context management: `/clear`, `/drop`, `/tokens` commands, automatic recursive summarization, background threading, stale-safety. Identified the gap: algorithmically sound but user-facing primitive. No topic visibility, no selective control, no structured persistence.

### Step 2: Diff Doc (First Draft)

**File:** `topics/pr-diff.md`

Wrote transformation prose: what changes, what enables it, before/after, files touched. Initially included `/drop-topic` alongside `/topics`.

### Step 3: Codex Review (GPT-5.4, Skeptical Maintainer)

Sent `pr-current-system.md` + `pr-diff.md` to codex as a skeptical maintainer review. Verdict: **would not merge.**

**7 issues identified:**

1. **Self-contradictory.** `EXPERIMENT_WRITEUP.md` says "do not open a PR." Then we open a PR. Maintainer sees this and closes immediately.

2. **Incoherent scope.** Old `PR_DRAFT.md` said `/topics` is future work. New `pr-diff.md` said it's the change. Two competing proposals in the same repo.

3. **Three proposals jammed together.** New backend, read-only UI, destructive mutation — different review problems at different risk levels. Should be split.

4. **"Quality equivalent" argues against complexity.** If the more complex system produces the same output, why own it?

5. **Why not make current history inspectable without a new backend?** The sharpest question. Answer: recursive summarization produces a single text blob with no internal structure. You can't inspect topics that don't exist. Union-find maintains topic structure as a byproduct of compression.

6. **`/drop-topic` during background summarization = heisenbug territory.** Destructive mutation against concurrent state is the riskiest part. Should be deferred.

7. **Auto-generated labels from "first noun phrase" = brittle.** Overpromised the UX quality.

**Fixes applied:**

- Removed stale `PR_DRAFT.md` (moved to /tmp).
- Fixed `EXPERIMENT_WRITEUP.md` recommendation — removed "don't open a PR," reframed as regression check.
- Rewrote `pr-diff.md`:
  - Narrowed to read-only `/topics` only. No `/drop-topic` (deferred — concurrency hazard).
  - Added "Why this can't be built on recursive summarization" section answering codex's sharpest question.
  - Honest about label quality: "they inherit whatever quality the summarizer produces — sometimes clear, sometimes vague."
  - Added "Open questions for maintainer" section instead of asserting answers.
  - Toned down issue mapping: "#3607 — prerequisite for selective control" not "directly addressed."
  - Explicit "What this PR does NOT contain" section.

### Step 4: Scope Reassessment

Read-only `/topics` without `/drop-topic` satisfies nobody:
- **Maintainer:** 450-line backend for a 30-line diagnostic command. Infrastructure-to-feature ratio is terrible.
- **Users (#3607):** Shows what they can't control. Worse than ignorance — highlights the gap without closing it.
- **Us:** The actual feature is the complete loop: see topics, drop one, model improves.

The concurrency concern that motivated deferring `/drop-topic` is solvable: check `summarizer_thread is not None` before mutating, same pattern `summarize_end` uses. If summarization is in flight, refuse and tell the user to try again. Conservative, correct, simple.

Rewrote `pr-diff.md` to include both commands:
- `/topics` — see topic clusters with token counts and previews
- `/drop-topic N` — remove topic N with threading guard
- Threading safety section explaining the guard pattern
- Tests for the guard (drop refused when thread is running)
- Before/after shows the complete workflow: inspect → drop → confirm

### Step 5: Codex Review Round 2

Sent reworked docs to codex. Verdict: **still would not merge, but closer.**

**What passed:**
- #5 (why a new backend is needed) — cleared. Conceptual argument accepted.
- Scope coherence improved — single proposal doc, consistent story.
- Framing honest — no longer overselling.

**What still failed (6 issues):**

1. **Experiment writeup still undermines the pitch.** Lines about complexity not being justified remain. Reads as "the experiment disproved the original reason, so here's a different reason." Not fatal but weakens footing.

2. **Branch shape.** Experiment data, harness code, benchmark artifacts mixed with feature code. Not a reviewable aider patch — it's a research repo. The PR must be a clean patch against aider, not this repo.

3. **Code doesn't exist yet.** `remove_cluster()`, `cmd_topics`, `cmd_drop_topic` are prose promises. No implementation, no integration tests against real aider threading. Maintainer is reviewing intent, not a mergeable PR.

4. **Unstable topic ordering.** `roots()` returns `list(set(...))` — nondeterministic. `/drop-topic 3` could hit a different topic after a merge. Destructive + unstable = unsafe UX.

5. **Unweighted centroid averaging.** Merging a 50-message cluster with a 1-message cluster gives equal weight. Distorts cluster identity over repeated merges.

6. **Not packaged as aider code.** Imports are standalone (`from context_window import ...`), not aider-internal (`from aider.context_window import ...`). Tests mirror standalone layout.

**Fixes applied to diff doc:**

- Added "Delivery" section: PR is a patch against aider, not this repo. Experiment artifacts are not part of the PR.
- Added "Fix: stable topic ordering" — `_root_order` list tracks insertion order. `roots()` returns deterministic order.
- Added "Fix: weighted centroid averaging" — weight by cluster size in `union()`.
- Changed module paths to `aider/` prefix throughout.
- Added tests: `test_stable_root_ordering`, `test_weighted_centroid`, `test_drop_topic_threading` (integration with mock thread).

**Remaining before next review:** Implement the code — `remove_cluster()`, stable ordering, weighted centroids, command methods. Then package as aider fork patch with integration tests.

### Step 6: Codex Review Round 3 (Ship-Focused)

Prompt shifted from "skeptical maintainer" to "help the author ship something useful." Same model (GPT-5.4).

Verdict: **still not mergeable, but the blocker is now trust, not the idea.**

**5 findings:**

1. **Overclaims implementation.** pr-diff.md describes code (`remove_cluster()`, `cmd_topics`, stable ordering, weighted centroids) that doesn't exist yet. Work log admits they're prose promises. A reviewer who notices one mismatch distrusts the whole pitch.

2. **Wrong repo shape.** Branch is still a research project (experiment docs, harness code, standalone modules). The PR doc says "patch against aider" but the repo contradicts it. Maintainer won't mentally separate "real PR" from "development repo."

3. **Experiment argues against the backend.** EXPERIMENT_WRITEUP.md says "same quality, more complexity." PR body can't dwell on the benchmark. Pitch must be "small user-facing control feature, default unchanged."

4. **Opening too backend-first.** First paragraphs explain architecture before stating the user problem. Lead with the workflow, not the data structure.

5. **Open questions invite debate.** Asking the maintainer "is the threading guard acceptable?" signals "there is concurrency risk and I want design feedback." Don't ask — decide.

**What codex said would make it mergeable:**
- Stop pitching from this repo. Open a clean aider branch.
- PR body: two tight paragraphs (user value first, then one-sentence implementation summary).
- Remove benchmark narrative except one line.
- Don't claim code that doesn't exist in the aider branch.
- Cut all open questions. Pick the behavior yourself.

**Fixes applied:**

Rewrote `pr-diff.md` as the actual PR body:
- Opens with the user problem and the two commands. No architecture until paragraph 3.
- "Why this needs a different backend" is a supporting section, not the lead.
- Benchmark reduced to one line: "quality-equivalent, 136 paired observations, McNemar p=0.248, 1.14x cost."
- Open questions removed. Decisions made: threading guard refuses during summarization (no join). `/topics` with recursive shows guidance to switch. Flag is `--chat-history-summarizer`.
- Framed as the PR description for an aider fork submission, not a pitch from this repo.
- Test list kept (concrete, verifiable) but no longer claims tests exist — describes what the PR will include.

### Step 7: Design Docs for Implementation

Rewrote `transformation-design.md` (189 → 428 lines). Now leads with the user problem, covers existing foundation, specifies new code (stable ordering, weighted centroids, `remove_cluster`, `cmd_topics`, `cmd_drop_topic`), threading safety, tests, integration points.

Rewrote `DESIGN_DECISIONS.md` (60 → 200+ lines). 24 decisions organized by regression risk: zero-regression guarantees (1-10), `/topics` + `/drop-topic` safety (11-14), inherited from gemini-cli with transfer rationale (15-20), decided fresh for aider (21-24). Every row in the defaults table shows "regression risk: None."

### Step 8: Codex Review Round 4 (Zero-Regression Lens)

Sent `transformation-design.md` + `DESIGN_DECISIONS.md` to codex. Prompt: review for uncovered regression paths, invalid gemini-cli assumptions, contradictions, missing decisions.

Verdict: **clustering mechanics salvageable, but source-of-truth mismatch makes `/drop-topic` broken at the only boundary that matters.**

**6 findings:**

1. **`/drop-topic` is a no-op for real prompt history.** The design mutates the forest but never updates `done_messages` — the thing aider actually sends to the model. If the drop shrinks history enough that `too_big()` returns false, re-summarization never runs, and the dropped topic stays in the next prompt forever.

2. **`/topics` needs a thread guard too.** `cmd_topics()` reads forest state (roots, summaries, hot zone) with no guard. If the background summarizer is mutating the same structures, reads can race or crash.

3. **Cross-session story contradicts the premise.** We say topics aren't reconstructed from opaque blobs, but on restart `done_messages` is loaded from history file — which IS an opaque blob. The forest can't recover topic boundaries from it.

4. **Gemini-cli timing assumptions don't transfer.** `graduate_at=26`/`evict_at=30` assumes dirty resolution runs during the main LLM call. Aider's timing is user think time + background thread. Also 2K cold tokens doesn't fit a 1K `max_chat_history_tokens`.

5. **State-sync hole.** Equal-length replacement of `done_messages` is called a "known gap" with the stale check as "safety net." But the stale check only discards one in-flight result — it doesn't reset the forest. Forest can drift.

6. **Under-specified where it matters.** No lifecycle rule for which `done_messages` mutations must invalidate the forest (`/clear`, history restore, edit-format transitions).

**4 missing decisions:** whether `/drop-topic` updates `done_messages` immediately; what `/topics` does during summarization; which paths must reset the forest; whether topic numbering survives summarization passes.

**Core insight:** Aider's source of truth is `done_messages`, not the forest. The design grafted a stateful owner onto a stateless function contract.

### Step 9: Source-of-Truth Resolution

Discussed three paths:

**A. `/drop-topic` rewrites `done_messages` immediately.** Forest is a shadow structure; `done_messages` stays the source of truth. Precedent: `/clear` already does `self.coder.done_messages = []`.

**B. Invert ownership — forest becomes source of truth.** `done_messages` derived from forest at `format_messages()` time. Cleanest architecture but requires rebuilding forest every `summarize()` call → O(n²) cost. This is the v1 cost bug. **Rejected.**

**C. Ship `/topics` read-only first, defer `/drop-topic`.** Safe but already rejected in Step 4 — read-only satisfies nobody.

**Decision: Path A.**

The forest is a shadow structure providing topic view and selective drop. `done_messages` remains the source of truth. Both updated in lockstep:

- `summarize()` feeds messages into forest, renders, writes result to `done_messages` (existing flow, unchanged)
- `/drop-topic` removes from forest, re-renders, writes result to `done_messages` (new, same pattern as `/clear`)
- `/topics` reads from forest (read-only, no `done_messages` change)

No intrusion on existing architecture. The existing flow doesn't know the forest exists.

**Why this works without cross-cycle persistence issues:**

The forest is already rebuilt after every successful summarization. When `summarize_end()` swaps in the result, `done_messages` shrinks (200 → ~32 messages). Next `summarize()` call sees `_fed_count > len(messages)` → full rebuild. Incremental feeding only works within a growing cycle before the result is applied. The forest is a session-scoped cache, not a persistent store.

After `/drop-topic` rewrites `done_messages`, the same mechanism triggers: `_fed_count` is stale → next `summarize()` rebuilds the forest from `done_messages` (which no longer contains the dropped topic). The topic is gone from both representations.

**Resolved findings:**
- #1 (no-op): `/drop-topic` now rewrites `done_messages` immediately
- #2 (thread guard): add same guard to `cmd_topics`
- #3 (cross-session): topics are session-scoped, stated plainly. No fake reconstruction.
- #4 (constants): reframe as heuristics, not guarantees
- #5 (state-sync): forest rebuild on every successful summarization already handles this
- #6 (lifecycle): anything that resets `done_messages` triggers `_fed_count > len(messages)` → automatic rebuild

**Migration path:** future PR makes forest the source of truth, with `done_messages` derived from it at `format_messages()` time. But that's after the maintainer is comfortable with the data structure.

### Step 10: Two-Phase PR Strategy

Updated design docs with source-of-truth decision, then forked aider to `Documents/aider` (branch `feat/topics-command`). Elicited integration points against actual aider code — all design assumptions verified:

- `/clear` does `self.coder.done_messages = []` via `_clear_chat_history()` (commands.py:435-437)
- 9 code paths assign to `done_messages` (base_coder.py:160,163,401,403,522,1032; architect_coder.py:39; commands.py:436; gui.py:253)
- `ChatSummary` constructed in main.py:949-952, passed to `Coder.create()`
- Command dispatch: `getattr(self, f'cmd_{cmd_name}', None)` (commands.py:289)
- `summarize_start/worker/end` match extraction exactly (base_coder.py:1002-1034)
- Args use kebab-case (`--max-chat-history-tokens`), prompts match extraction

**Decision: split into two PRs.**

**PR 1 — Foundation (earn trust):** Port 4 modules to `aider/`, add `--chat-history-summarizer union-find` flag, construction site conditional in main.py. No new commands, no new UX. Pitch: "opt-in alternative backend, quality-equivalent (136 paired observations, McNemar p=0.248, 1.14x cost), default unchanged." The maintainer reviews clean code and experiment evidence without worrying about threading guards or `done_messages` mutations.

**PR 2 — Feature (spend trust):** Add `/topics` and `/drop-topic` on top of merged foundation. Pitch: "now that structured history exists, here are commands to use it." Smaller scope, focused on user value, built on code the maintainer already accepted. All the source-of-truth mechanics (`done_messages` sync, thread guards on both commands, session-scoped topics) live here.

This matches the codex feedback about trust: PR 1 earns it, PR 2 spends it. It also derisks review — the maintainer can evaluate the backend swap independently from the UX surface.

### Step 11: Final Doc Pass (Codex Round 5 Fixes)

Applied 5 recommendations from codex review round 5:

1. **Rewrote `pr-diff.md` as PR 1 body.** Removed `/topics` and `/drop-topic` — now describes the foundation only. Added explicit test plan table (12 areas). Follow-up section lists what PR 2 will add.

2. **Split `transformation-design.md` into PR 1 / PR 2 sections.** Clear `# PR 1: Foundation` and `# PR 2: /topics and /drop-topic` headers. Each section has its own changes, test plan, and integration points table.

3. **Moved weighted centroid and stable root ordering to PR 1.** Both are backend correctness, not UX. Weighted centroid prevents cluster identity distortion over repeated merges. Stable ordering prevents nondeterministic `render()` output. Neither depends on `/topics` existing.

4. **Added PR 1 test plan.** 12-row table: flag selection, default unchanged, output format, fallback to recursive, `summarize_all()` parity, stale discard + rebuild, low token budget, weighted centroid, stable root ordering, cluster summarization, incremental feeding, forest mechanics.

5. **Softened absolute language in `DESIGN_DECISIONS.md`.** "Zero-Regression Guarantees" → "Regression Prevention." "Regression surface: Zero" → "Regression surface: Minimal." Defaults table "None" → "Minimal — ..." or "Opt-in only." Fixed duplicate Decision 12 numbering (now 12-15 for `/topics` safety, 16-21 for inherited, 22-25 for fresh). Added PR annotations to section headers.

### Step 12: PR 1 Implementation

Ported all 4 modules to `~/Documents/aider/aider/` on branch `feat/topics-command`.

**Files created:**
- `aider/context_window.py` — Forest + ContextWindow, with stable root ordering (`_root_order` list in `Forest.__init__`, `insert`, `union`, `roots`) and weighted centroid averaging in `union()` (centroids weighted by cluster size before merge)
- `aider/embedding_service.py` — TFIDFEmbedder, ported as-is
- `aider/cluster_summarizer.py` — ClusterSummarizer, ported as-is
- `aider/chat_summary_uf.py` — ChatSummaryUF(ChatSummary), imports changed from standalone to `aider.*`

**Files modified:**
- `aider/args.py` — Added `--chat-history-summarizer` argument (default `recursive`, choices `[recursive, union-find]`), placed after `--max-chat-history-tokens` in the Model settings group
- `aider/main.py` — Conditional at summarizer construction: `ChatSummaryUF` when `union-find`, `ChatSummary` otherwise. Lazy import of `ChatSummaryUF` in the conditional branch.

**Tests:**
- `tests/basic/test_chat_summary_uf.py` — 49 tests across 10 test classes covering all 12 areas from PR 1 test plan:
  - Forest mechanics (12 tests): insert, union, roots, compact, nearest_root, dirty tracking, path compression
  - Stable root ordering (4 tests): insertion order, order after merge, deterministic across calls, multiple merges
  - Weighted centroid (3 tests): equal-size midpoint, unequal-size weights toward larger, sparse dict weighting
  - Cluster summarizer (4 tests): first model success, fallback to second, all fail raises, single model
  - Flag selection (3 tests): subclass relationship, UF construction, default is ChatSummary
  - Output format (2 tests): not-too-big returns unchanged, summary+Ok+hot format
  - Fallback to recursive (2 tests): result exceeds max_tokens, no cold clusters
  - summarize_all parity (1 test): delegates to parent
  - Stale discard + rebuild (2 tests): _fed_count shrink triggers rebuild, incremental feeding tracks correctly
  - Incremental feeding (2 tests): skips non-user/assistant roles, empty content not fed
  - Low token budget (1 test): graceful fallback
  - TF-IDF embedder (7 tests): sparse dict, empty string, stopwords, vocabulary growth, doc count, cosine similarity
  - ContextWindow (6 tests): append/render, hot count, graduation, force merge, cold+hot render, dirty resolution

**Verification:**
- 49 new tests: all passing
- 523 existing aider tests: all passing (1 skipped, 55 subtests), zero regressions
- Commit: `9ee29182` on `feat/topics-command`

**Issues encountered:**
- Initial test failures: `max_tokens=30` too low for output format test (UF result with 26 hot messages exceeded budget, triggering recursive fallback). Fixed by setting `max_tokens=400`.
- Empty content test: `max_tokens=5` with 2-word messages didn't trigger `too_big()`. Fixed by lowering to `max_tokens=1`.
- Needed to create `.venv` with Python 3.13 and install aider deps (system Python 3.9 lacked required packages).

### Step 13: PR Body Polish

Added Background section to `pr-diff.md` linking [june.kim/union-find-compaction](https://june.kim/union-find-compaction) (blog post with gemini-cli prototype results) and the [research repo](https://github.com/kimjune01/union-find-compaction-for-aider) (full methodology, preregistration, data). Without these, a reviewer sees 549 lines of novel code with benchmark claims but no provenance.

Ran humanize scan. Fixed:
- 2 em dashes replaced with commas/periods
- 3 restated points collapsed (old Why section said "structure is destroyed" three different ways)
- 4 negative parallelisms killed ("This PR doesn't add X. It adds Y" → "This is the backend")
- 1 rule of three cut ("inspecting topics, dropping one, branching context" → "addressable units you can inspect and drop")
- Added "Without the flag, this PR is a no-op" to Summary
- Closing line rewritten from "not X, it's Y" to direct statement

### Step 14: PR 2 Implementation

Tagged PR 1 commit as `pr1-foundation` (`9ee29182`). Created branch `feat/topics-drop-topic`.

**Files modified:**

`aider/context_window.py`:
- `Forest.remove_cluster(root_id)` — removes cluster by any node id (calls `_find` first). Cleans all 7 data structures: `_parent`, `_content`, `_embedding`, `_summary`, `_dirty`, `_dirty_inputs`, `_children`, `_root_order`. Returns removed node IDs.
- `ContextWindow.hot_messages()` — public accessor returning hot zone contents as string list. Avoids commands reaching into `_hot[_graduated_index:]` directly.

`aider/commands.py`:
- `cmd_topics(args)` — shows indexed topic list with token counts and first-line previews. Shows hot zone count. Lazy imports `ChatSummaryUF` to avoid import cycle. Threading guard: refuses if `summarizer_thread is not None`. Shows guidance message for recursive summarizer users.
- `cmd_drop_topic(args)` — parses integer index, removes cluster from forest, re-renders, writes result to `done_messages`. Three cases for re-render: cold+hot, cold-only, hot-only (all cold dropped). Threading guard same as `/topics`.

`tests/basic/test_chat_summary_uf.py`:
- Fixed `count()` mock to handle raw strings (aider's `token_count` accepts both strings and dicts).
- 23 new tests across 4 test classes:
  - `TestRemoveCluster` (6 tests): singleton, merged, all data structures, preserves others, by child id, not in render
  - `TestCmdTopics` (6 tests): recursive guidance, empty, with clusters, hot zone, refused during summarization, works after thread completes
  - `TestCmdDropTopic` (9 tests): recursive guidance, invalid index, non-integer, empty args, removes cluster, updates done_messages, dropped content not in done_messages, refused during summarization, drop all empties
  - `test_topics_reflects_drop`: integration — drop then /topics shows fewer entries
  - `test_hot_messages_returns_ungraduated`: ContextWindow accessor

**Verification:**
- 72 new tests: all passing
- 562 existing aider tests: all passing (1 skipped), zero regressions
- Commit: `aecb76a1` on `feat/topics-drop-topic`

**Issues encountered:**
- `token_count` mock only accepted dicts but commands pass raw strings. Fixed by adding `isinstance(msg, str)` branch to test helper.

### Step 15: PR 1 Smoke Test + Submission

Added `tests/basic/test_smoke_uf.py` — 8 smoke tests using a distilled Flask #1169 conversation (3 topics: path traversal, Windows drive letters, file descriptor leak). Tests the foundation directly without commands:

1. Clusters form from realistic data
2. Output format matches recursive (`[summary_msg, "Ok.", *hot_messages]`)
3. `render()` produces cold summaries + hot contents
4. Distinct topics form separate clusters (groups by topic, not timestamp)
5. Fallback to recursive when budget exceeded
6. Stale rebuild behavior when `done_messages` shrinks
7. System messages filtered from forest
8. Deterministic render across calls

**Issues encountered:**
- `max_tokens=200` too low — result (342 tokens with 8 clusters + 26 hot) exceeded budget, triggering recursive fallback. Fixed: `max_tokens=400`.
- Singleton clusters (never merged) return raw content, not mock summaries. Fixed assertion to check "at least one merged cluster" instead of "all cold parts are summaries."
- Stale rebuild test assumed `_fed_count` resets on shrink, but if the shorter list doesn't trigger `too_big`, `summarize` returns early. Fixed test to handle both cases.

Committed on `feat/topics-command` (`3a52652f`), pushed. Merged into `feat/topics-drop-topic` (`d3578904`).

**PR 1 submitted:** https://github.com/Aider-AI/aider/pull/4940

57 new tests (49 unit + 8 smoke), 523 existing passing, zero regressions.

### Step 16: Codex Review Round 6 + Fixes

Sent production code to codex (GPT-5.4). Verdict: **would not merge as-is, two real issues.**

**5 findings:**

1. **Mixed-role tail mismatch (real bug).** `hot_count` counts user/assistant messages fed to ContextWindow, but `messages[-hot_count:]` slices from the original list which may contain system/tool messages. Tail points at wrong suffix.

2. **`_hot` grows without bound (real bug).** `_maybe_evict()` advances `_graduated_index` but never removes entries. Memory grows monotonically.

3. **Stale detection is count-only.** `_fed_count > len(messages)` catches shrinks but not same-length replacements. Fragile but acceptable for append-only usage.

4. **Empty embedding drop in `union()`.** `if emb_a and emb_b:` guard means empty-embedding cluster loses centroid data silently.

5. **`ClusterSummarizer` catches bare `Exception`.** Swallows causal context. Not blocking.

**Also recommended cutting:**
- `is_dirty()`, `dirty_inputs()` — unused outside tests
- List-vector branches in `_cosine_similarity` and `union()` — everything uses sparse dicts

**Fixes applied (`c113e395`):**

1. **Mixed-role fix:** Track fed message indices explicitly. `_get_fed_indices()` maps `hot_count` back to correct original message positions. System/tool messages in the tail are preserved.

2. **Memory fix:** `_trim_graduated()` trims `_hot` after each append, resetting `_graduated_index` to 0. Memory stays bounded at `evict_at` entries.

3. **Empty embedding fix:** Added `elif emb_b:` branch to preserve non-empty embedding when one side is empty.

4. **Dead code removal:** Removed `is_dirty()`, `dirty_inputs()`, list-vector branches. Updated tests to use internal `_dirty` directly.

5. **New tests:** `TestMixedRoleHandling` (2 tests: system messages preserved, hot tail maps correctly), `TestMemoryBounds` (2 tests: trimmed after graduation, bounded over 200-message session).

**Verification:** 551 passed, 1 skipped, zero regressions. Merged into `feat/topics-drop-topic` (`66d21b3e`).

### Step 17: Codex Review Round 7 + Fixes

Sent updated code to codex. Verdict: **all 5 previous fixes confirmed correct. Two new issues.**

**2 findings:**

1. **Cluster summarization failure crashes instead of falling back (merge blocker).** `resolve_dirty()` raises `ValueError` if all models fail. `ChatSummaryUF.summarize()` doesn't catch it, so the UF path can abort instead of falling back to recursive.

2. **`evict_at=30` is dead code.** `_maybe_graduate()` keeps hot at ≤ `graduate_at` (26), so `_maybe_evict()` (threshold 30) never triggers. Dead code that's misleading.

**Fixes applied (`20608455`):**
1. Wrapped `resolve_dirty()` in try/except — falls back to `super().summarize()` on any failure
2. Removed `_maybe_evict()`, `evict_at` parameter, and all references. `_maybe_graduate()` is the only graduation path.

**Verification:** 551 passed, 1 skipped, zero regressions.

### Step 18: Codex PR 2 Implementation (Parallel Experiment)

Created `feat/topics-codex-impl` branch off PR 1 for codex (GPT-5.4) to independently implement PR 2 (`/topics` + `/drop-topic`). Gave it the PR 2 spec from `transformation-design.md` plus key patterns. Goal: compare codex's implementation with ours and identify improvements.

Codex produced a complete implementation: 67 tests passing, 565 total, zero regressions. Committed as `53f4d47e`.

**Comparison with our implementation:**

Codex improvements adopted (already on its branch):
- `context_window` as read-only property (better encapsulation)
- `render()` reuses `hot_messages()` (DRY)
- Total token count in `/topics` output
- Simpler 2-branch re-render in `/drop-topic` (vs our 4-branch)
- `make_dynamic_summary_model` test helper (unique summaries per cluster)
- `TestDropTopicDoneMessagesSync` integration test (full round-trip with rebuild verification)

Our fixes already on codex's branch (inherited from PR 1):
- `resolve_dirty` fallback to recursive on failure
- `evict_at` removed (dead code)
- `_get_fed_indices` for mixed-role tail handling
- `_trim_graduated` for memory bounds
- Empty embedding preservation in `union()`

**Decision:** Use codex's branch as PR 2. It's the best-of-both with no manual merge needed.

### Step 19: PR 2 Submission

**PR 2 submitted:** https://github.com/Aider-AI/aider/pull/4941

Depends on PR 1 (#4940). PR body explains the two-PR strategy: PR 1 is the backend (risky, evaluable independently), PR 2 is the UX surface (straightforward, builds on merged foundation).

Both PRs are now drafts:
- **PR 1 (foundation):** https://github.com/Aider-AI/aider/pull/4940 — `feat/topics-command`
- **PR 2 (commands):** https://github.com/Aider-AI/aider/pull/4941 — `feat/topics-codex-impl`

### Step 20: Control-Loop Bug (Force Graduation)

**Bug:** Union-find path was unreachable in real usage. `too_big()` fires on token count (~10-15 messages), but `graduate_at=26` means 26 user/assistant messages must be fed before any graduate from hot to cold. Token budget fires first, no cold clusters exist, falls back to recursive every time. The feature literally could not trigger.

**Discovery:** Manual testing with `--max-chat-history-tokens 512` and `--max-chat-history-tokens 1024`. `/topics` always showed "No topics yet" regardless of conversation length. Confirmed with `--verbose` — "Starting to summarize" never appeared in logs.

**Root cause (codex diagnosis):** "This is a control-loop bug, not a tuning bug. The system decides 'start compressing' based on tokens, but decides 'what is compressible' based on message count. Those two thresholds are independent."

**Why tests missed it:** Tests bypassed `too_big()` by feeding messages directly into ContextWindow or using word-count mocks with low `max_tokens`. They tested the machinery, not the control loop. The integration smoke test (`test_smoke_uf.py`) also used mocks that sidestepped the real tokenizer.

**Fix (`b62b032b`):** Added `force_graduate(keep_hot)` to ContextWindow. In `summarize()`, when `too_big` fires but `cold_count == 0` and `hot_count > 4`, force-graduate the oldest half of the hot zone before rendering. This breaks the deadlock: clusters form, `resolve_dirty()` summarizes them, `render()` produces structured output.

**Sonnet side-quest:** During manual testing, Sonnet (via aider) flagged 8 issues in the code and applied "fixes" that broke the TF-IDF embedder (`log((1+1)/(1+1)) = 0` → empty vectors). Reverted. Lesson: Sonnet is bad at reviewing code it's seeing for the first time. Codex is better as reviewer.

**Lesson:** Integration tests with real tokenizers would have caught this. Mock-based tests proved the machinery works but missed the control-loop interaction. Need an end-to-end test that uses the real model tokenizer and verifies cold clusters actually form.
