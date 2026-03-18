"""Tests for experiment harness — pipeline orchestrator."""

import json
from pathlib import Path

from harness import ExperimentHarness
from schemas import Conversation, Judgment, RecallQuestion, RunResult


def _conv(cid="conv-0"):
    return Conversation(cid, "t/t", 1, "Bug", [{"role": "user", "content": "msg"}])

def _question(cid="conv-0"):
    return RecallQuestion("q1", cid, "Q?", "A", "filename", "early")

def _run_result(cid="conv-0", system="union_find"):
    return RunResult(cid, system, [{"role": "user", "content": "summary"}], 100.0, 500, 200)

def _judgment(cid="conv-0"):
    return Judgment(cid, "q1", "union_find", "CORRECT", "reason")


class TestExperimentHarness:
    def test_init(self, tmp_path):
        assert ExperimentHarness(tmp_path).state.stage == "init"

    def test_checkpoint_roundtrip(self, tmp_path):
        h = ExperimentHarness(tmp_path)
        h.state.stage = "generation"
        h.state.conversations = [_conv()]
        h.checkpoint()
        h2 = ExperimentHarness(tmp_path)
        h2.load_checkpoint()
        assert h2.state.stage == "generation"

    def test_save_conversations(self, tmp_path):
        h = ExperimentHarness(tmp_path)
        h.save_conversations([_conv("c1"), _conv("c2")])
        assert len(list((tmp_path / "conversations").glob("*.json"))) == 2

    def test_save_load_questions(self, tmp_path):
        h = ExperimentHarness(tmp_path)
        h.save_questions("conv-0", [_question()])
        loaded = h.load_questions("conv-0")
        assert len(loaded) == 1

    def test_save_load_run_result(self, tmp_path):
        h = ExperimentHarness(tmp_path)
        h.save_run_result(_run_result())
        loaded = h.load_run_result("conv-0", "union_find")
        assert loaded.system == "union_find"

    def test_save_load_judgments(self, tmp_path):
        h = ExperimentHarness(tmp_path)
        h.save_judgments("conv-0", [_judgment()])
        loaded = h.load_judgments("conv-0")
        assert len(loaded) == 1

    def test_completed_conversation_ids(self, tmp_path):
        h = ExperimentHarness(tmp_path)
        assert h.get_completed_conversation_ids() == []
        h.save_conversations([_conv("conv-0")])
        assert "conv-0" in h.get_completed_conversation_ids()
