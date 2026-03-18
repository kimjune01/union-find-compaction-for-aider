"""Token tracking and cost accounting for experiment LLM calls.

TrackedModel wraps aider's Model and calls send_completion() directly
to capture response.usage that simple_send_with_retries() discards.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LLMCallRecord:
    """A single LLM call with token usage."""

    system: str
    role: str
    prompt_tokens: int
    completion_tokens: int
    model: str

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "role": self.role,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LLMCallRecord:
        return cls(
            system=data["system"],
            role=data["role"],
            prompt_tokens=data["prompt_tokens"],
            completion_tokens=data["completion_tokens"],
            model=data["model"],
        )


@dataclass
class TokenTracker:
    """Accumulates LLM call records and computes aggregates."""

    records: list[LLMCallRecord] = field(default_factory=list)

    def record(self, rec: LLMCallRecord) -> None:
        self.records.append(rec)

    def aggregate(self) -> dict[str, dict[str, int]]:
        """Aggregate by system label."""
        result: dict[str, dict[str, int]] = {}
        for rec in self.records:
            if rec.system not in result:
                result[rec.system] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "call_count": 0,
                }
            result[rec.system]["prompt_tokens"] += rec.prompt_tokens
            result[rec.system]["completion_tokens"] += rec.completion_tokens
            result[rec.system]["call_count"] += 1
        return result

    def aggregate_by_role(self) -> dict[str, dict[str, int]]:
        """Aggregate by role (compression, generation, etc.)."""
        result: dict[str, dict[str, int]] = {}
        for rec in self.records:
            if rec.role not in result:
                result[rec.role] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "call_count": 0,
                }
            result[rec.role]["prompt_tokens"] += rec.prompt_tokens
            result[rec.role]["completion_tokens"] += rec.completion_tokens
            result[rec.role]["call_count"] += 1
        return result

    def save(self, path: Path) -> None:
        data = [r.to_dict() for r in self.records]
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> TokenTracker:
        data = json.loads(path.read_text())
        tracker = cls()
        tracker.records = [LLMCallRecord.from_dict(d) for d in data]
        return tracker


class TrackedModel:
    """Wraps an aider Model, intercepts LLM calls to capture token usage.

    Aider's simple_send_with_retries() discards response.usage.
    We call send_completion() directly and extract usage from the response.
    Then we proxy all other attributes to the underlying model so it can
    be used as a drop-in replacement.
    """

    def __init__(self, model, system: str, role: str, tracker: TokenTracker) -> None:
        self._model = model
        self._system = system
        self._role = role
        self._tracker = tracker

    def simple_send_with_retries(self, messages):
        """Call send_completion() to capture usage, return text like the original."""
        _hash, response = self._model.send_completion(
            messages=messages, functions=None, stream=False
        )

        prompt_tokens = 0
        completion_tokens = 0
        if hasattr(response, "usage") and response.usage is not None:
            prompt_tokens = getattr(response.usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(response.usage, "completion_tokens", 0) or 0

        self._tracker.record(
            LLMCallRecord(
                system=self._system,
                role=self._role,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                model=self._model.name if hasattr(self._model, "name") else str(self._model),
            )
        )

        # Extract text from response (same as aider's simple_send_with_retries)
        try:
            return response.choices[0].message.content
        except (AttributeError, IndexError):
            return None

    def token_count(self, messages):
        """Proxy to underlying model's token_count."""
        return self._model.token_count(messages)

    def __getattr__(self, name):
        """Proxy all other attributes to the underlying model."""
        return getattr(self._model, name)
