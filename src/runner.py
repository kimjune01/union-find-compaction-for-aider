"""Experiment runner — runs both compression systems on conversations.

Uses aider's ChatSummary (recursive baseline) and ChatSummaryUF (union-find).
TrackedModel wraps aider's Model to capture token usage via send_completion().
Instruments ContextWindow methods for H2 latency reporting.
"""

from __future__ import annotations

import time
from typing import Any

from schemas import Conversation, RunResult
from token_tracker import TokenTracker, TrackedModel


class ExperimentRunner:
    """Runs both compression systems on a conversation and collects metrics."""

    def __init__(
        self,
        models,
        max_tokens: int = 1024,
        tracker: TokenTracker | None = None,
    ) -> None:
        self._models = models if isinstance(models, list) else [models]
        self._max_tokens = max_tokens
        self._tracker = tracker or TokenTracker()

    def run_union_find(self, conv: Conversation) -> RunResult:
        """Run union-find compression (ChatSummaryUF) on a conversation."""
        if not conv.messages:
            return RunResult(conv.id, "union_find", [], 0.0, 0, 0)

        # Wrap models with tracking
        tracked = [TrackedModel(m, "union_find", "compression", self._tracker) for m in self._models]

        from chat_summary_uf import ChatSummaryUF
        summarizer = ChatSummaryUF(models=tracked, max_tokens=self._max_tokens)

        # Instrument context window for H2 latency
        append_times: list[float] = []
        render_times: list[float] = []
        resolve_times: list[float] = []
        self._instrument_context_window(summarizer.context_window, append_times, render_times, resolve_times)

        # Feed messages incrementally, triggering compression when too_big()
        messages = list(conv.messages)
        start = time.monotonic()

        accumulated: list[dict[str, str]] = []
        result_messages = messages  # default if never compressed
        for msg in messages:
            accumulated.append(msg)
            if summarizer.too_big(accumulated):
                result_messages = summarizer.summarize(accumulated)
                accumulated = result_messages

        elapsed_ms = (time.monotonic() - start) * 1000

        # Tally tokens from tracker
        uf_records = [r for r in self._tracker.records if r.system == "union_find"]
        total_prompt = sum(r.prompt_tokens for r in uf_records)
        total_completion = sum(r.completion_tokens for r in uf_records)

        return RunResult(
            conversation_id=conv.id,
            system="union_find",
            compressed_history=result_messages,
            compression_time_ms=elapsed_ms,
            total_prompt_tokens=total_prompt,
            total_completion_tokens=total_completion,
            append_latencies_ms=append_times,
            render_latencies_ms=render_times,
            resolve_dirty_latencies_ms=resolve_times,
        )

    def run_recursive(self, conv: Conversation) -> RunResult:
        """Run recursive compression (ChatSummary baseline) on a conversation."""
        if not conv.messages:
            return RunResult(conv.id, "recursive", [], 0.0, 0, 0)

        tracked = [TrackedModel(m, "recursive", "compression", self._tracker) for m in self._models]

        from aider.history import ChatSummary
        summarizer = ChatSummary(models=tracked, max_tokens=self._max_tokens)

        messages = list(conv.messages)
        start = time.monotonic()

        accumulated: list[dict[str, str]] = []
        result_messages = messages
        for msg in messages:
            accumulated.append(msg)
            if summarizer.too_big(accumulated):
                result_messages = summarizer.summarize(accumulated)
                accumulated = result_messages

        elapsed_ms = (time.monotonic() - start) * 1000

        rec_records = [r for r in self._tracker.records if r.system == "recursive"]
        total_prompt = sum(r.prompt_tokens for r in rec_records)
        total_completion = sum(r.completion_tokens for r in rec_records)

        return RunResult(
            conversation_id=conv.id,
            system="recursive",
            compressed_history=result_messages,
            compression_time_ms=elapsed_ms,
            total_prompt_tokens=total_prompt,
            total_completion_tokens=total_completion,
        )

    def run_both(self, conv: Conversation) -> tuple[RunResult, RunResult]:
        """Run both systems on the same conversation."""
        uf_result = self.run_union_find(conv)
        rec_result = self.run_recursive(conv)
        return uf_result, rec_result

    def _instrument_context_window(self, cw, append_times, render_times, resolve_times):
        """Monkey-patch ContextWindow methods to capture H2 latency."""
        original_append = cw.append
        original_render = cw.render
        original_resolve = cw.resolve_dirty

        def timed_append(content):
            t0 = time.monotonic()
            result = original_append(content)
            append_times.append((time.monotonic() - t0) * 1000)
            return result

        def timed_render():
            t0 = time.monotonic()
            result = original_render()
            render_times.append((time.monotonic() - t0) * 1000)
            return result

        def timed_resolve():
            t0 = time.monotonic()
            result = original_resolve()
            resolve_times.append((time.monotonic() - t0) * 1000)
            return result

        cw.append = timed_append
        cw.render = timed_render
        cw.resolve_dirty = timed_resolve
