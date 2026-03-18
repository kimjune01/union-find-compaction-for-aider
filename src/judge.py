"""Blinded judge via codex exec with GPT-5.4."""

from __future__ import annotations

import hashlib
import subprocess

from prompts_experiment import JUDGE_PROMPT
from schemas import Judgment, RecallQuestion


class BlindedJudge:
    """Judges recall questions using codex exec with GPT-5.4.

    A/B assignment is randomized per conversation (not per question) and
    deterministic given the seed, so results are reproducible.
    """

    def __init__(self, seed: int = 42) -> None:
        self._seed = seed
        self._assignment_cache: dict[str, tuple[str, str]] = {}

    def get_ab_assignment(self, conversation_id: str) -> tuple[str, str]:
        """Return (system_a, system_b) — deterministic given seed + conversation_id."""
        if conversation_id in self._assignment_cache:
            return self._assignment_cache[conversation_id]

        h = hashlib.sha256(f"{self._seed}:{conversation_id}".encode()).hexdigest()
        if int(h, 16) % 2 == 0:
            assignment = ("union_find", "recursive")
        else:
            assignment = ("recursive", "union_find")

        self._assignment_cache[conversation_id] = assignment
        return assignment

    def judge_question(
        self,
        compressed_history: str,
        question: str,
        expected_answer: str,
    ) -> tuple[str, str]:
        """Judge a single question. Returns (verdict, reasoning)."""
        prompt = JUDGE_PROMPT.format(
            compressed_history=compressed_history,
            question=question,
            expected_answer=expected_answer,
        )

        try:
            result = subprocess.run(
                ["codex", "exec", "-c", 'model="gpt-5.4"', "--ephemeral", "-"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return "INCORRECT", "Judge process failed"

        if result.returncode != 0:
            return "INCORRECT", f"Judge exited with code {result.returncode}"

        return self._parse_verdict(result.stdout.strip())

    def _parse_verdict(self, output: str) -> tuple[str, str]:
        lines = output.strip().split("\n")
        last_line = lines[-1].strip().upper() if lines else ""

        if last_line == "CORRECT":
            return "CORRECT", "\n".join(lines[:-1]).strip()
        elif last_line == "INCORRECT":
            return "INCORRECT", "\n".join(lines[:-1]).strip()
        else:
            return "INCORRECT", f"Could not parse verdict from: {output}"

    def judge_conversation(
        self,
        conversation_id: str,
        questions: list[RecallQuestion],
        compressed_histories: dict[str, str],
    ) -> list[Judgment]:
        """Judge all questions for a conversation across both systems (blinded)."""
        system_a, system_b = self.get_ab_assignment(conversation_id)
        judgments: list[Judgment] = []

        for label, system in [("A", system_a), ("B", system_b)]:
            history = compressed_histories[system]
            for q in questions:
                verdict, reasoning = self.judge_question(
                    compressed_history=history,
                    question=q.question,
                    expected_answer=q.expected_answer,
                )
                judgments.append(
                    Judgment(conversation_id, q.id, system, verdict, reasoning)
                )

        return judgments
