"""Question generator — 8 recall questions per conversation."""

from __future__ import annotations

import json
import re

import litellm

from prompts_experiment import GENERATE_QUESTIONS
from schemas import Conversation, RecallQuestion
from token_tracker import TokenTracker, LLMCallRecord


class QuestionGenerator:
    def __init__(self, model_name: str = "gemini/gemini-3.1-flash-lite-preview",
                 tracker: TokenTracker | None = None,
                 model=None) -> None:
        self._model_name = model_name
        self._model = model  # backward compat for tests
        self._tracker = tracker or TokenTracker()

    def parse_questions(self, raw: str, conversation_id: str) -> list[RecallQuestion]:
        text = raw.strip()
        match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse questions JSON: {e}") from e
        if not isinstance(data, list):
            raise ValueError("Expected JSON array of questions")
        return [
            RecallQuestion(
                id=item["id"], conversation_id=conversation_id,
                question=item["question"], expected_answer=item["expected_answer"],
                category=item["category"], region=item["region"],
            )
            for item in data
        ]

    def validate_count(self, questions: list[RecallQuestion], expected: int = 8) -> bool:
        return len(questions) == expected

    def validate_categories(self, questions: list[RecallQuestion], min_categories: int = 3) -> bool:
        return len({q.category for q in questions}) >= min_categories

    def validate_regions(self, questions: list[RecallQuestion], min_per_region: int = 2) -> bool:
        counts: dict[str, int] = {}
        for q in questions:
            counts[q.region] = counts.get(q.region, 0) + 1
        return all(counts.get(r, 0) >= min_per_region for r in ("early", "mid", "late"))

    def build_prompt(self, conv: Conversation) -> str:
        conversation_text = "\n".join(f"{m['role']}: {m['content']}" for m in conv.messages)
        return GENERATE_QUESTIONS.format(conversation=conversation_text)

    def _call_llm(self, messages: list[dict[str, str]], max_tokens: int = 4096) -> str:
        """Call LLM via litellm directly."""
        resp = litellm.completion(
            model=self._model_name,
            messages=messages,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        usage = resp.usage
        self._tracker.record(LLMCallRecord(
            system="generation", role="question_generation",
            prompt_tokens=usage.prompt_tokens or 0,
            completion_tokens=usage.completion_tokens or 0,
            model=self._model_name,
        ))
        return content

    def generate_questions(self, conv: Conversation, max_retries: int = 3) -> list[RecallQuestion]:
        prompt = self.build_prompt(conv)
        for _ in range(max_retries):
            raw = self._call_llm([{"role": "user", "content": prompt}])
            try:
                questions = self.parse_questions(raw, conv.id)
            except ValueError:
                continue
            if self.validate_count(questions) and self.validate_categories(questions) and self.validate_regions(questions):
                return questions
        raise RuntimeError(f"Failed to generate valid questions for {conv.id} after {max_retries} retries")
