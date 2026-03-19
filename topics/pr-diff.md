# PR 1: Add opt-in union-find chat history summarizer

**Part 1 of 2.** This PR adds the backend. [Part 2](#4941) adds `/topics` and `/drop-topic` commands on top of it. Split for reviewability — together they give users selective control over chat history.

**Submitted:** https://github.com/Aider-AI/aider/pull/4940

## Summary

- Adds `--chat-history-summarizer union-find` flag, opt-in alternative to the default recursive summarizer
- Groups messages into topic clusters by TF-IDF similarity, summarizes each cluster independently
- Produces the same output format, uses the same model cascade, falls back to recursive if output exceeds budget
- Without the flag, this PR is a no-op. Default behavior completely unchanged
- Benchmarked as quality-equivalent (136 paired observations, McNemar p=0.248, 1.14× cost)
- No new dependencies (pure Python TF-IDF embedder)

## Why

The current recursive summarizer compresses chat history into a single opaque text blob. Original messages are discarded. There's no provenance, and no way to selectively remove one stale topic without re-summarizing everything.

- #3607 — selective history control ("I want to drop the debugging context but keep the refactor decisions")
- #2219 — see/edit context ("I want to see what the model's context actually contains")
- #948 — token breakdown with actions ("show me what's consuming tokens and let me act on it")

Union-find compaction groups messages into topic-coherent clusters and summarizes each one independently. Every summary traces back to its source messages through `find()`. Topics become addressable units you can inspect and drop.

This is the backend. User-facing commands (`/topics`, `/drop-topic`) come in a follow-up PR once the foundation is reviewed.

## Background

The algorithm was [prototyped against gemini-cli](https://june.kim/union-find-compaction), where it showed a +8–18pp recall advantage over flat summarization across 7 trials (1 significant at p=0.039, rest directional). It was then ported to aider for validation against aider's stronger recursive baseline. Full experiment methodology, preregistration, and data are in the [research repo](https://github.com/kimjune01/union-find-compaction-for-aider).

## Future paths

The new code is fully contained — 4 new files, no modifications to base_coder.py, no changes to the threading model, no new state that other systems depend on. The opt-in flag keeps all three options cheap:

- **Make it the default:** Change `default="recursive"` to `default="union-find"` in `args.py`. One line.
- **Rip it out:** Delete 4 files, remove 15 lines from `args.py` + `main.py`. Five minutes.
- **Keep as-is:** Zero maintenance. The recursive path doesn't know the union-find path exists.

---

# PR 2: Add /topics and /drop-topic commands

**Depends on PR 1.** Review after that PR merges.

**Submitted:** https://github.com/Aider-AI/aider/pull/4941

## Summary

- `/topics` — see what the model remembers, with token counts and previews
- `/drop-topic N` — remove one stale topic without wiping everything
- Both commands refuse during background summarization (threading guard)
- Without `--chat-history-summarizer union-find`, both show a guidance message

## How /drop-topic works

`done_messages` is the source of truth. The forest is a shadow structure. `/drop-topic` updates both in lockstep:

1. Remove the cluster from the forest
2. Re-render cold summaries + hot contents
3. Write the result to `self.coder.done_messages`

The model immediately stops seeing the dropped topic. On the next `summarize()` call, `_fed_count > len(messages)` triggers a full forest rebuild from the new `done_messages`.

---

# Review history

7 rounds of codex review (GPT-5.4) across both PRs. Key issues found and fixed:

1. Mixed-role tail mismatch — `hot_count` mapped to wrong original messages when system/tool messages present. Fixed with `_get_fed_indices()`.
2. `_hot` list grew without bound — graduated entries never released. Fixed with `_trim_graduated()`.
3. `resolve_dirty()` failure crashed instead of falling back to recursive. Fixed with try/except.
4. `evict_at` was dead code — `_maybe_graduate()` already kept hot zone bounded. Removed.
5. Empty embedding dropped silently in `union()`. Fixed with `elif emb_b:` fallback.
6. Unused methods (`is_dirty`, `dirty_inputs`) and dead list-vector branches. Removed.

Codex also independently implemented PR 2 from the spec (`feat/topics-codex-impl`). We compared both implementations and adopted codex's improvements: `context_window` as read-only property, `render()` reusing `hot_messages()`, simpler 2-branch re-render in `/drop-topic`, total token count in `/topics` output.
