"""Pipeline orchestrator with JSON checkpointing and futility rules."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from analyzer import ExperimentAnalyzer, cost_ratio
from schemas import Conversation, ExperimentState, Judgment, RecallQuestion, RunResult

logger = logging.getLogger(__name__)


class ExperimentHarness:
    CHECKPOINT_FILE = "state.json"

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir or Path("experiment/data")
        self.state = ExperimentState()
        self._analyzer = ExperimentAnalyzer()

    def checkpoint(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self.state.save(self._data_dir / self.CHECKPOINT_FILE)

    def load_checkpoint(self) -> bool:
        path = self._data_dir / self.CHECKPOINT_FILE
        if path.exists():
            self.state = ExperimentState.load(path)
            return True
        return False

    def save_conversations(self, conversations: list[Conversation]) -> None:
        d = self._data_dir / "conversations"
        d.mkdir(parents=True, exist_ok=True)
        for conv in conversations:
            (d / f"{conv.id}.json").write_text(json.dumps(conv.to_dict(), indent=2))

    def load_conversation(self, conv_id: str) -> Conversation:
        return Conversation.from_dict(json.loads(
            (self._data_dir / "conversations" / f"{conv_id}.json").read_text()
        ))

    def save_questions(self, conv_id: str, questions: list[RecallQuestion]) -> None:
        d = self._data_dir / "conversations" / "questions"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{conv_id}.json").write_text(json.dumps([q.to_dict() for q in questions], indent=2))

    def load_questions(self, conv_id: str) -> list[RecallQuestion]:
        data = json.loads((self._data_dir / "conversations" / "questions" / f"{conv_id}.json").read_text())
        return [RecallQuestion.from_dict(d) for d in data]

    def save_run_result(self, result: RunResult) -> None:
        d = self._data_dir / "quality"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{result.system}-{result.conversation_id}.json").write_text(
            json.dumps(result.to_dict(), indent=2)
        )

    def load_run_result(self, conv_id: str, system: str) -> RunResult:
        return RunResult.from_dict(json.loads(
            (self._data_dir / "quality" / f"{system}-{conv_id}.json").read_text()
        ))

    def save_judgments(self, conv_id: str, judgments: list[Judgment]) -> None:
        d = self._data_dir / "quality"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"judgments-{conv_id}.json").write_text(
            json.dumps([j.to_dict() for j in judgments], indent=2)
        )

    def load_judgments(self, conv_id: str) -> list[Judgment]:
        data = json.loads((self._data_dir / "quality" / f"judgments-{conv_id}.json").read_text())
        return [Judgment.from_dict(d) for d in data]

    def get_completed_conversation_ids(self) -> list[str]:
        d = self._data_dir / "conversations"
        if not d.exists():
            return []
        return [p.stem for p in d.glob("*.json")]

    def run_pipeline(
        self,
        conversations: list[Conversation],
        questions_per_conv: dict[str, list[RecallQuestion]],
        runner,
        judge,
        futility_check_at: int = 12,
    ) -> None:
        """Run the full experiment pipeline with inline judging and futility."""
        all_judgments: list[Judgment] = []

        for i, conv in enumerate(conversations):
            logger.info(f"Processing conversation {i+1}/{len(conversations)}: {conv.id}")

            uf_result, rec_result = runner.run_both(conv)
            self.save_run_result(uf_result)
            self.save_run_result(rec_result)

            questions = questions_per_conv.get(conv.id, [])

            # Build compressed history text for judge
            def _to_text(msgs):
                return "\n".join(f"{m.get('role','')}: {m.get('content','')}" for m in msgs)

            compressed = {
                "union_find": _to_text(uf_result.compressed_history),
                "recursive": _to_text(rec_result.compressed_history),
            }
            judgments = judge.judge_conversation(conv.id, questions, compressed)
            self.save_judgments(conv.id, judgments)
            all_judgments.extend(judgments)

            self.state.completed_conversations.append(conv.id)
            self.checkpoint()

            if (i + 1) == futility_check_at and all_judgments:
                mcnemar = self._analyzer.analyze_judgments(all_judgments)
                agg = runner._tracker.aggregate()
                uf_tokens = agg.get("union_find", {}).get("prompt_tokens", 0)
                rec_tokens = agg.get("recursive", {}).get("prompt_tokens", 0)
                ratio = cost_ratio(float(uf_tokens), float(rec_tokens))
                futility = self._analyzer.check_futility(mcnemar, i + 1, ratio)
                if futility.should_stop:
                    logger.warning(f"Futility stop: {futility.reason}")
                    break

        self.state.stage = "complete"
        self.state.judgments = all_judgments
        self.checkpoint()
