"""Dataclasses and JSON serialization for experiment data."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Conversation:
    """A synthetic multi-turn coding conversation derived from a GitHub issue."""

    id: str
    repo: str
    issue_number: int
    title: str
    messages: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "repo": self.repo,
            "issue_number": self.issue_number,
            "title": self.title,
            "messages": self.messages,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Conversation:
        return cls(
            id=data["id"],
            repo=data["repo"],
            issue_number=data["issue_number"],
            title=data["title"],
            messages=data["messages"],
        )


@dataclass
class RecallQuestion:
    """A factual recall question about a conversation."""

    VALID_CATEGORIES = frozenset(
        {
            "filename",
            "function_name",
            "library",
            "error_message",
            "decision",
            "configuration",
            "api_endpoint",
            "test_case",
        }
    )

    id: str
    conversation_id: str
    question: str
    expected_answer: str
    category: str
    region: str  # "early", "mid", "late"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "question": self.question,
            "expected_answer": self.expected_answer,
            "category": self.category,
            "region": self.region,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecallQuestion:
        return cls(
            id=data["id"],
            conversation_id=data["conversation_id"],
            question=data["question"],
            expected_answer=data["expected_answer"],
            category=data["category"],
            region=data["region"],
        )


@dataclass
class RunResult:
    """Result of running a compression system on a conversation."""

    conversation_id: str
    system: str  # "recursive" or "union_find"
    compressed_history: list[dict[str, str]]
    compression_time_ms: float
    total_prompt_tokens: int
    total_completion_tokens: int
    append_latencies_ms: list[float] = field(default_factory=list)
    render_latencies_ms: list[float] = field(default_factory=list)
    resolve_dirty_latencies_ms: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "system": self.system,
            "compressed_history": self.compressed_history,
            "compression_time_ms": self.compression_time_ms,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "append_latencies_ms": self.append_latencies_ms,
            "render_latencies_ms": self.render_latencies_ms,
            "resolve_dirty_latencies_ms": self.resolve_dirty_latencies_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunResult:
        return cls(
            conversation_id=data["conversation_id"],
            system=data["system"],
            compressed_history=data["compressed_history"],
            compression_time_ms=data["compression_time_ms"],
            total_prompt_tokens=data["total_prompt_tokens"],
            total_completion_tokens=data["total_completion_tokens"],
            append_latencies_ms=data.get("append_latencies_ms", []),
            render_latencies_ms=data.get("render_latencies_ms", []),
            resolve_dirty_latencies_ms=data.get("resolve_dirty_latencies_ms", []),
        )


@dataclass
class Judgment:
    """Judge verdict for a single question against a single system."""

    conversation_id: str
    question_id: str
    system: str
    verdict: str  # "CORRECT" or "INCORRECT"
    judge_reasoning: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "question_id": self.question_id,
            "system": self.system,
            "verdict": self.verdict,
            "judge_reasoning": self.judge_reasoning,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Judgment:
        return cls(
            conversation_id=data["conversation_id"],
            question_id=data["question_id"],
            system=data["system"],
            verdict=data["verdict"],
            judge_reasoning=data["judge_reasoning"],
        )


@dataclass
class ExperimentState:
    """Checkpointable experiment state for resume support."""

    stage: str = "init"
    completed_conversations: list[str] = field(default_factory=list)
    conversations: list[Conversation] = field(default_factory=list)
    questions: list[RecallQuestion] = field(default_factory=list)
    run_results: list[RunResult] = field(default_factory=list)
    judgments: list[Judgment] = field(default_factory=list)

    def save(self, path: Path) -> None:
        data = {
            "stage": self.stage,
            "completed_conversations": self.completed_conversations,
            "conversations": [c.to_dict() for c in self.conversations],
            "questions": [q.to_dict() for q in self.questions],
            "run_results": [r.to_dict() for r in self.run_results],
            "judgments": [j.to_dict() for j in self.judgments],
        }
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> ExperimentState:
        data = json.loads(path.read_text())
        return cls(
            stage=data["stage"],
            completed_conversations=data["completed_conversations"],
            conversations=[Conversation.from_dict(c) for c in data["conversations"]],
            questions=[RecallQuestion.from_dict(q) for q in data["questions"]],
            run_results=[RunResult.from_dict(r) for r in data["run_results"]],
            judgments=[Judgment.from_dict(j) for j in data["judgments"]],
        )
