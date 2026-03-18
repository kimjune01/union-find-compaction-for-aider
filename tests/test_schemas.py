"""Tests for experiment data schemas."""

import json
from pathlib import Path

from schemas import Conversation, ExperimentState, Judgment, RecallQuestion, RunResult


def _sample_conversation():
    return Conversation(
        id="flask-1361", repo="pallets/flask", issue_number=1361,
        title="render_template bug",
        messages=[{"role": "user", "content": "Found a bug."}, {"role": "assistant", "content": "Details?"}],
    )

def _sample_question():
    return RecallQuestion("q1", "flask-1361", "What module?", "flask.templating", "filename", "early")

def _sample_run_result():
    return RunResult("flask-1361", "union_find", [{"role": "user", "content": "summary"}], 150.5, 1000, 500)

def _sample_judgment():
    return Judgment("flask-1361", "q1", "union_find", "CORRECT", "Found the answer.")


class TestConversation:
    def test_roundtrip_json(self):
        conv = _sample_conversation()
        restored = Conversation.from_dict(conv.to_dict())
        assert restored.id == conv.id
        assert restored.messages == conv.messages

    def test_json_string_roundtrip(self):
        conv = _sample_conversation()
        restored = Conversation.from_dict(json.loads(json.dumps(conv.to_dict())))
        assert len(restored.messages) == 2


class TestRecallQuestion:
    def test_roundtrip(self):
        q = _sample_question()
        restored = RecallQuestion.from_dict(q.to_dict())
        assert restored.question == q.question
        assert restored.category in RecallQuestion.VALID_CATEGORIES


class TestRunResult:
    def test_roundtrip(self):
        r = _sample_run_result()
        restored = RunResult.from_dict(r.to_dict())
        assert restored.system == "union_find"
        assert restored.compressed_history == r.compressed_history

    def test_latency_fields(self):
        r = RunResult("c1", "union_find", [], 10.0, 0, 0,
                      append_latencies_ms=[0.1, 0.2], render_latencies_ms=[0.5])
        restored = RunResult.from_dict(r.to_dict())
        assert restored.append_latencies_ms == [0.1, 0.2]


class TestJudgment:
    def test_roundtrip(self):
        j = _sample_judgment()
        restored = Judgment.from_dict(j.to_dict())
        assert restored.verdict == "CORRECT"


class TestExperimentState:
    def test_save_and_load(self, tmp_path):
        state = ExperimentState(
            stage="generation", completed_conversations=["flask-1361"],
            conversations=[_sample_conversation()], questions=[_sample_question()],
            run_results=[_sample_run_result()], judgments=[_sample_judgment()],
        )
        state.save(tmp_path / "state.json")
        loaded = ExperimentState.load(tmp_path / "state.json")
        assert loaded.stage == "generation"
        assert len(loaded.conversations) == 1

    def test_empty_state(self):
        state = ExperimentState()
        assert state.stage == "init"
        assert state.conversations == []
