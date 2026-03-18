"""Tests for token tracking — TrackedModel wraps aider's Model.send_completion()."""

from pathlib import Path
from unittest.mock import MagicMock

from token_tracker import LLMCallRecord, TokenTracker, TrackedModel


class TestLLMCallRecord:
    def test_total_tokens(self):
        rec = LLMCallRecord("union_find", "compression", 100, 50, "gpt-4o-mini")
        assert rec.total_tokens == 150

    def test_roundtrip(self):
        rec = LLMCallRecord("recursive", "compression", 200, 100, "gpt-4o-mini")
        restored = LLMCallRecord.from_dict(rec.to_dict())
        assert restored.prompt_tokens == 200


class TestTokenTracker:
    def test_record_and_aggregate(self):
        tracker = TokenTracker()
        tracker.record(LLMCallRecord("union_find", "compression", 100, 50, "m"))
        tracker.record(LLMCallRecord("union_find", "compression", 200, 100, "m"))
        tracker.record(LLMCallRecord("recursive", "compression", 150, 75, "m"))
        agg = tracker.aggregate()
        assert agg["union_find"]["prompt_tokens"] == 300
        assert agg["union_find"]["call_count"] == 2
        assert agg["recursive"]["call_count"] == 1

    def test_save_and_load(self, tmp_path):
        tracker = TokenTracker()
        tracker.record(LLMCallRecord("union_find", "compression", 100, 50, "m"))
        tracker.save(tmp_path / "tokens.json")
        loaded = TokenTracker.load(tmp_path / "tokens.json")
        assert len(loaded.records) == 1

    def test_empty_aggregate(self):
        assert TokenTracker().aggregate() == {}

    def test_aggregate_by_role(self):
        tracker = TokenTracker()
        tracker.record(LLMCallRecord("union_find", "compression", 100, 50, "m"))
        tracker.record(LLMCallRecord("union_find", "generation", 200, 100, "m"))
        agg = tracker.aggregate_by_role()
        assert agg["compression"]["prompt_tokens"] == 100
        assert agg["generation"]["prompt_tokens"] == 200


class TestTrackedModel:
    def _mock_model(self, prompt_tokens=100, completion_tokens=50, content="Summary"):
        """Create a mock aider Model with send_completion()."""
        model = MagicMock()
        model.name = "gpt-4o-mini"

        response = MagicMock()
        response.usage.prompt_tokens = prompt_tokens
        response.usage.completion_tokens = completion_tokens
        response.choices = [MagicMock()]
        response.choices[0].message.content = content

        model.send_completion.return_value = ("hash", response)
        model.token_count.return_value = 10
        return model

    def test_calls_send_completion_and_records(self):
        tracker = TokenTracker()
        model = self._mock_model()
        tracked = TrackedModel(model, "union_find", "compression", tracker)

        result = tracked.simple_send_with_retries([{"role": "user", "content": "test"}])

        model.send_completion.assert_called_once()
        assert result == "Summary"
        assert len(tracker.records) == 1
        assert tracker.records[0].prompt_tokens == 100
        assert tracker.records[0].completion_tokens == 50

    def test_records_system_label(self):
        tracker = TokenTracker()
        tracked = TrackedModel(self._mock_model(), "recursive", "compression", tracker)
        tracked.simple_send_with_retries([{"role": "user", "content": "test"}])
        assert tracker.records[0].system == "recursive"

    def test_handles_missing_usage(self):
        tracker = TokenTracker()
        model = MagicMock()
        model.name = "test"
        response = MagicMock()
        response.usage = None
        response.choices = [MagicMock()]
        response.choices[0].message.content = "Result"
        model.send_completion.return_value = ("hash", response)

        tracked = TrackedModel(model, "union_find", "compression", tracker)
        result = tracked.simple_send_with_retries([{"role": "user", "content": "test"}])
        assert result == "Result"
        assert tracker.records[0].prompt_tokens == 0

    def test_proxies_token_count(self):
        tracker = TokenTracker()
        model = self._mock_model()
        model.token_count.return_value = 42
        tracked = TrackedModel(model, "union_find", "compression", tracker)
        assert tracked.token_count("hello") == 42

    def test_proxies_other_attributes(self):
        tracker = TokenTracker()
        model = self._mock_model()
        model.info = {"max_tokens": 4096}
        tracked = TrackedModel(model, "union_find", "compression", tracker)
        assert tracked.info == {"max_tokens": 4096}
