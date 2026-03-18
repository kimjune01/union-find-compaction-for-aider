# Work Log: `/topics` for Aider

## Context

Foundation complete (see `WORK_LOG-foundational.md`). Union-find compaction works, passes regression (same recall as recursive, 1.14x cost, sub-ms latency), 145 tests. But the experiment showed no quality advantage — the value is structural, not algorithmic.

Issue research on `paul-gauthier/aider` (~73 issues, 235+ comments) revealed users want visibility and control over context, not better summaries. Top issues: #3607 (selective history control), #2219 (see/edit context), #948 (token breakdown with actions), #4079 (cross-session persistence).

The feature: `/topics` — show what topic clusters are in context with token counts, and let users selectively drop them. This is the most requested unbuilt feature, and it requires topic-structured context that only union-find provides.

## 2026-03-18

### Step 1: Extract Current System

**File:** `topics/pr-current-system.md`

Documented aider's current context management: `/clear`, `/drop`, `/tokens` commands, automatic recursive summarization, background threading, stale-safety. Identified the gap: algorithmically sound but user-facing primitive. No topic visibility, no selective control, no structured persistence.
