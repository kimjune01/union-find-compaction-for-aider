#!/usr/bin/env python3
"""Run the preregistered experiment: 24 conversations, 192 paired observations."""

import json
import logging
import os
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from aider.models import Model

from analyzer import ExperimentAnalyzer, compute_latency_percentiles, compute_sign_test, cost_ratio
from conversation_generator import ConversationGenerator
from harness import ExperimentHarness
from judge import BlindedJudge
from question_generator import QuestionGenerator
from runner import ExperimentRunner
from schemas import Conversation, Judgment, RunResult
from token_tracker import TokenTracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# Suppress litellm verbose logging
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("litellm").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Per prereg: Python-heavy repos with real issues (not PRs)
REPOS = [
    "pallets/flask",
    "tiangolo/fastapi",
    "django/django",
    "pytorch/pytorch",
    "microsoft/vscode",
    "psf/requests",
    "encode/httpx",
    "python/cpython",
]
ISSUES_PER_REPO = 4  # 4 × 8 repos = 32 targets, enough to get 24 after failures
DATA_DIR = Path("experiment/data")
MODEL_NAME = "gemini/gemini-3.1-flash-lite-preview"


def stage_1_conversations(harness, model, tracker):
    """Generate 24 conversations from GitHub issues."""
    log.info("=== Stage 1: Generate Conversations ===")
    conv_gen = ConversationGenerator(model_name=MODEL_NAME, tracker=tracker)
    conversations = []
    existing_ids = harness.get_completed_conversation_ids()

    for repo in REPOS:
        log.info(f"Fetching issues from {repo}")
        try:
            issues = conv_gen.fetch_issues(repo, limit=ISSUES_PER_REPO)
        except RuntimeError as e:
            log.warning(f"  Failed to fetch from {repo}: {e}")
            continue

        for issue in issues:
            if len(conversations) >= 24:
                break
            conv_id = conv_gen.build_conversation_id(issue["repo"], issue["issue_number"])
            if conv_id in existing_ids:
                log.info(f"  Skipping {conv_id} (already generated)")
                conversations.append(harness.load_conversation(conv_id))
                continue

            log.info(f"  Generating conversation for {conv_id}")
            for attempt in range(3):
                try:
                    conv = conv_gen.generate_conversation(issue)
                    harness.save_conversations([conv])
                    conversations.append(conv)
                    log.info(f"  Generated {len(conv.messages)} messages for {conv_id}")
                    break
                except Exception as e:
                    if "ServiceUnavailable" in str(type(e).__name__) or "429" in str(e):
                        wait = 15 * (attempt + 1)
                        log.warning(f"  Rate limited, waiting {wait}s...")
                        time.sleep(wait)
                    else:
                        log.error(f"  Failed to generate {conv_id}: {e}")
                        break
            # Small delay between conversations to avoid rate limits
            time.sleep(3)

    log.info(f"Stage 1 complete: {len(conversations)} conversations")
    return conversations


def stage_2_questions(harness, model, tracker, conversations):
    """Generate 8 recall questions per conversation."""
    log.info("=== Stage 2: Generate Questions ===")
    q_gen = QuestionGenerator(model_name=MODEL_NAME, tracker=tracker)
    questions_per_conv = {}

    for conv in conversations:
        q_path = harness._data_dir / "conversations" / "questions" / f"{conv.id}.json"
        if q_path.exists():
            log.info(f"  Skipping questions for {conv.id} (already generated)")
            questions_per_conv[conv.id] = harness.load_questions(conv.id)
            continue

        log.info(f"  Generating questions for {conv.id}")
        try:
            questions = q_gen.generate_questions(conv)
            harness.save_questions(conv.id, questions)
            questions_per_conv[conv.id] = questions
            log.info(f"  Generated {len(questions)} questions for {conv.id}")
        except Exception as e:
            log.error(f"  Failed to generate questions for {conv.id}: {e}")

    log.info(f"Stage 2 complete: {sum(len(qs) for qs in questions_per_conv.values())} questions")
    return questions_per_conv


def stage_3_run_and_judge(harness, model, tracker, conversations, questions_per_conv):
    """Run both systems, judge inline, check futility at 12."""
    log.info("=== Stage 3: Run Systems + Judge ===")
    judge = BlindedJudge(seed=42)
    runner = ExperimentRunner(model, tracker=tracker, max_tokens=2048)
    analyzer = ExperimentAnalyzer()
    all_judgments = []

    for i, conv in enumerate(conversations):
        if conv.id not in questions_per_conv or not questions_per_conv[conv.id]:
            log.warning(f"  Skipping {conv.id} — no questions")
            continue

        # Check if already judged
        j_path = harness._data_dir / "quality" / f"judgments-{conv.id}.json"
        if j_path.exists():
            log.info(f"  Skipping {conv.id} (already judged)")
            all_judgments.extend(harness.load_judgments(conv.id))
            continue

        log.info(f"  [{i+1}/{len(conversations)}] Running {conv.id} ({len(conv.messages)} msgs)")

        # Run both systems
        t0 = time.monotonic()
        try:
            uf_result = runner.run_union_find(conv)
            rec_result = runner.run_recursive(conv)
        except Exception as e:
            log.error(f"  Runner failed on {conv.id}: {e}")
            continue
        elapsed = time.monotonic() - t0
        log.info(f"  Compression done in {elapsed:.1f}s — UF: {len(uf_result.compressed_history)} msgs, Rec: {len(rec_result.compressed_history)} msgs")

        harness.save_run_result(uf_result)
        harness.save_run_result(rec_result)

        # Judge
        def _to_text(msgs):
            return "\n".join(f"{m.get('role','')}: {m.get('content','')}" for m in msgs)

        compressed = {
            "union_find": _to_text(uf_result.compressed_history),
            "recursive": _to_text(rec_result.compressed_history),
        }

        questions = questions_per_conv[conv.id]
        log.info(f"  Judging {len(questions)} questions × 2 systems")
        judgments = judge.judge_conversation(conv.id, questions, compressed)
        harness.save_judgments(conv.id, judgments)
        all_judgments.extend(judgments)

        # Log per-conversation results
        uf_correct = sum(1 for j in judgments if j.system == "union_find" and j.verdict == "CORRECT")
        rec_correct = sum(1 for j in judgments if j.system == "recursive" and j.verdict == "CORRECT")
        log.info(f"  Scores — UF: {uf_correct}/{len(questions)}, Rec: {rec_correct}/{len(questions)}")

        harness.state.completed_conversations.append(conv.id)
        harness.checkpoint()

        # Futility check at 12
        completed_count = len(harness.state.completed_conversations)
        if completed_count == 12 and all_judgments:
            mcnemar = analyzer.analyze_judgments(all_judgments)
            agg = tracker.aggregate()
            uf_tokens = agg.get("union_find", {}).get("prompt_tokens", 0)
            rec_tokens = agg.get("recursive", {}).get("prompt_tokens", 0)
            ratio = cost_ratio(float(uf_tokens), float(rec_tokens))
            futility = analyzer.check_futility(mcnemar, completed_count, ratio)
            log.info(f"  Futility check: b={mcnemar.b}, c={mcnemar.c}, p={mcnemar.p_value:.3f}, cost={ratio:.2f}x")
            if futility.should_stop:
                log.warning(f"  FUTILITY STOP: {futility.reason}")
                break

    return all_judgments


def stage_4_analyze(harness, tracker, all_judgments):
    """Final analysis: McNemar's, sign test, latency, cost — per prereg."""
    log.info("=== Stage 4: Analysis ===")
    analyzer = ExperimentAnalyzer()

    # H1: Recall — McNemar's test
    mcnemar = analyzer.analyze_judgments(all_judgments)
    total = len(all_judgments) // 2  # paired
    uf_correct = sum(1 for j in all_judgments if j.system == "union_find" and j.verdict == "CORRECT")
    rec_correct = sum(1 for j in all_judgments if j.system == "recursive" and j.verdict == "CORRECT")
    effect_pp = (uf_correct - rec_correct) / max(total, 1) * 100

    log.info(f"H1 Recall: UF={uf_correct}/{total} ({uf_correct/max(total,1)*100:.1f}%), "
             f"Rec={rec_correct}/{total} ({rec_correct/max(total,1)*100:.1f}%)")
    log.info(f"  Effect: {effect_pp:+.1f}pp, McNemar chi2={mcnemar.chi2:.2f}, p={mcnemar.p_value:.4f}")
    log.info(f"  Discordant pairs: b={mcnemar.b} (UF+/Rec-), c={mcnemar.c} (Rec+/UF-)")

    # H1 sensitivity: conversation-level sign test (prereg)
    conv_ids = list({j.conversation_id for j in all_judgments})
    uf_conv_scores = []
    rec_conv_scores = []
    for cid in sorted(conv_ids):
        cj = [j for j in all_judgments if j.conversation_id == cid]
        n_q = len(cj) // 2
        uf_conv_scores.append(sum(1 for j in cj if j.system == "union_find" and j.verdict == "CORRECT") / max(n_q, 1))
        rec_conv_scores.append(sum(1 for j in cj if j.system == "recursive" and j.verdict == "CORRECT") / max(n_q, 1))
    sign = compute_sign_test(uf_conv_scores, rec_conv_scores)
    log.info(f"  Sign test: wins={sign['wins']}, losses={sign['losses']}, ties={sign['ties']}, p={sign['p_value']:.4f}")

    # H2: Latency — collect from all run results
    all_append = []
    all_render = []
    all_resolve = []
    quality_dir = harness._data_dir / "quality"
    if quality_dir.exists():
        for f in quality_dir.glob("union_find-*.json"):
            rr = RunResult.from_dict(json.loads(f.read_text()))
            all_append.extend(rr.append_latencies_ms)
            all_render.extend(rr.render_latencies_ms)
            all_resolve.extend(rr.resolve_dirty_latencies_ms)

    append_p = compute_latency_percentiles(all_append)
    render_p = compute_latency_percentiles(all_render)
    resolve_p = compute_latency_percentiles(all_resolve)

    # Prereg H2b: p50, p90, p95, max
    log.info(f"H2a Append:  p50={append_p['p50']:.2f}ms, p90={append_p['p90']:.2f}ms, p95={append_p['p95']:.2f}ms, max={append_p['max']:.2f}ms")
    log.info(f"H2a Render:  p50={render_p['p50']:.2f}ms, p90={render_p['p90']:.2f}ms, p95={render_p['p95']:.2f}ms, max={render_p['max']:.2f}ms")
    log.info(f"H2b Resolve: p50={resolve_p['p50']:.2f}ms, p90={resolve_p['p90']:.2f}ms, p95={resolve_p['p95']:.2f}ms, max={resolve_p['max']:.2f}ms")

    h2a_pass = append_p["p95"] < 100 and render_p["p95"] < 100

    # H3: Cost
    agg = tracker.aggregate()
    uf_total = agg.get("union_find", {}).get("prompt_tokens", 0) + agg.get("union_find", {}).get("completion_tokens", 0)
    rec_total = agg.get("recursive", {}).get("prompt_tokens", 0) + agg.get("recursive", {}).get("completion_tokens", 0)
    ratio = cost_ratio(float(uf_total), float(rec_total))

    log.info(f"H3 Cost: UF={uf_total} tokens, Rec={rec_total} tokens, ratio={ratio:.2f}x")
    h3_pass = ratio <= 2.0

    # Write RESULTS.md — matching prereg data storage spec
    results_path = harness._data_dir / "RESULTS.md"
    results = f"""# Experiment Results

**Date:** {time.strftime('%Y-%m-%d %H:%M')}
**Model:** {MODEL_NAME}
**Classification:** Exploratory benchmark validation
**Conversations:** {len(conv_ids)}
**Paired observations:** {total}

---

## H1: Recall

| System | Correct | Total | Rate |
|--------|---------|-------|------|
| Union-Find | {uf_correct} | {total} | {uf_correct/max(total,1)*100:.1f}% |
| Recursive | {rec_correct} | {total} | {rec_correct/max(total,1)*100:.1f}% |

**Effect:** {effect_pp:+.1f}pp
**McNemar's chi2:** {mcnemar.chi2:.4f}
**p-value:** {mcnemar.p_value:.4f}
**Discordant pairs:** b={mcnemar.b} (UF correct, Rec wrong), c={mcnemar.c} (Rec correct, UF wrong)
**Result:** {"PASS (>= +5pp, p<0.05)" if effect_pp >= 5 and mcnemar.p_value < 0.05 else "MISS"}

### Sensitivity: Conversation-level sign test

| UF wins | Rec wins | Ties | p-value |
|---------|----------|------|---------|
| {sign['wins']} | {sign['losses']} | {sign['ties']} | {sign['p_value']:.4f} |

## H2: Latency

### H2a: Append + Render (target: p95 < 100ms)

| Operation | p50 | p90 | p95 | max |
|-----------|-----|-----|-----|-----|
| Append | {append_p['p50']:.2f}ms | {append_p['p90']:.2f}ms | {append_p['p95']:.2f}ms | {append_p['max']:.2f}ms |
| Render | {render_p['p50']:.2f}ms | {render_p['p90']:.2f}ms | {render_p['p95']:.2f}ms | {render_p['max']:.2f}ms |

**Result:** {"PASS" if h2a_pass else "MISS"}

### H2b: ResolveDirty (informational, no pass/fail target)

| p50 | p90 | p95 | max |
|-----|-----|-----|-----|
| {resolve_p['p50']:.2f}ms | {resolve_p['p90']:.2f}ms | {resolve_p['p95']:.2f}ms | {resolve_p['max']:.2f}ms |

## H3: Cost (target: UF <= 2x recursive)

| System | Total Tokens |
|--------|-------------|
| Union-Find | {uf_total} |
| Recursive | {rec_total} |

**Ratio:** {ratio:.2f}x
**Result:** {"PASS" if h3_pass else "MISS"}

---

## Decision

| Hypothesis | Target | Result | Outcome |
|------------|--------|--------|---------|
| H1 Recall | >= +5pp, p<0.05 | {effect_pp:+.1f}pp, p={mcnemar.p_value:.4f} | {"PASS" if effect_pp >= 5 and mcnemar.p_value < 0.05 else "MISS"} |
| H2a Latency | p95 < 100ms | append={append_p['p95']:.2f}ms, render={render_p['p95']:.2f}ms | {"PASS" if h2a_pass else "MISS"} |
| H3 Cost | <= 2x | {ratio:.2f}x | {"PASS" if h3_pass else "MISS"} |
"""
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(results)
    log.info(f"Results written to {results_path}")

    # Save token tracker
    cost_dir = harness._data_dir / "cost"
    cost_dir.mkdir(parents=True, exist_ok=True)
    tracker.save(cost_dir / "tokens.json")

    return results


def main():
    # Set API key
    os.environ["GEMINI_API_KEY"] = "AIzaSyBi7FdnrZql_hxdOJCdcSFWHh3SW5wwA6A"

    log.info(f"Model: {MODEL_NAME}")
    model = Model(MODEL_NAME)
    tracker = TokenTracker()
    harness = ExperimentHarness(DATA_DIR)

    # Resume if possible
    if harness.load_checkpoint():
        log.info(f"Resumed from checkpoint: stage={harness.state.stage}, "
                 f"completed={len(harness.state.completed_conversations)}")

    # Stage 1: Conversations
    conversations = stage_1_conversations(harness, model, tracker)
    if len(conversations) < 12:
        log.error(f"Only {len(conversations)} conversations — need at least 12")
        return

    harness.state.stage = "questions"
    harness.checkpoint()

    # Stage 2: Questions
    questions_per_conv = stage_2_questions(harness, model, tracker, conversations)

    harness.state.stage = "running"
    harness.checkpoint()

    # Stage 3: Run + Judge
    all_judgments = stage_3_run_and_judge(harness, model, tracker, conversations, questions_per_conv)

    # Stage 4: Analyze
    stage_4_analyze(harness, tracker, all_judgments)

    harness.state.stage = "complete"
    harness.checkpoint()
    log.info("Experiment complete.")


if __name__ == "__main__":
    main()
