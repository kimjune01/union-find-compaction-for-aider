"""Tests for experiment runner — uses mock aider models."""

from unittest.mock import MagicMock, patch

from runner import ExperimentRunner
from schemas import Conversation, RunResult
from token_tracker import TokenTracker


def _mock_model(content="Summary", prompt_tokens=100, completion_tokens=50):
    model = MagicMock()
    model.name = "test-model"
    response = MagicMock()
    response.usage.prompt_tokens = prompt_tokens
    response.usage.completion_tokens = completion_tokens
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    model.send_completion.return_value = ("hash", response)
    model.simple_send_with_retries.return_value = content
    model.token_count.return_value = 10
    return model


def _make_conversation(n: int = 40):
    msgs = [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"Message {i} about debugging ORM"} for i in range(n)]
    return Conversation("test-1", "test/test", 1, "Bug", msgs)


class TestExperimentRunner:
    def test_run_union_find_returns_result(self):
        tracker = TokenTracker()
        runner = ExperimentRunner(_mock_model(), tracker=tracker)
        result = runner.run_union_find(_make_conversation())
        assert isinstance(result, RunResult)
        assert result.system == "union_find"

    def test_run_recursive_returns_result(self):
        tracker = TokenTracker()
        model = _mock_model()
        # Patch at source — import is deferred inside run_recursive()
        with patch("aider.history.ChatSummary") as MockCS:
            instance = MockCS.return_value
            instance.too_big.return_value = False
            runner = ExperimentRunner(model, tracker=tracker)
            result = runner.run_recursive(_make_conversation())
        assert result.system == "recursive"

    def test_run_both(self):
        tracker = TokenTracker()
        model = _mock_model()
        with patch("aider.history.ChatSummary") as MockCS:
            instance = MockCS.return_value
            instance.too_big.return_value = False
            runner = ExperimentRunner(model, tracker=tracker)
            uf, rec = runner.run_both(_make_conversation())
        assert uf.system == "union_find"
        assert rec.system == "recursive"

    def test_empty_conversation(self):
        tracker = TokenTracker()
        runner = ExperimentRunner(_mock_model(), tracker=tracker)
        result = runner.run_union_find(Conversation("e", "t/t", 1, "E", []))
        assert result.compressed_history == []

    def test_union_find_captures_latencies(self):
        tracker = TokenTracker()
        # Low max_tokens so too_big() fires (40 msgs × 10 tokens = 400 > 50)
        runner = ExperimentRunner(_mock_model(), tracker=tracker, max_tokens=50)
        result = runner.run_union_find(_make_conversation())
        # Append latencies should be populated (one per message fed to context_window)
        assert len(result.append_latencies_ms) > 0

    def test_union_find_with_many_messages(self):
        tracker = TokenTracker()
        runner = ExperimentRunner(_mock_model(), tracker=tracker)
        result = runner.run_union_find(_make_conversation(100))
        assert result.compression_time_ms >= 0
