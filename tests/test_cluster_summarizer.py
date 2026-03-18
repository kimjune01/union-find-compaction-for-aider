"""Tests for ClusterSummarizer."""

from unittest.mock import MagicMock
import pytest

from cluster_summarizer import ClusterSummarizer


class TestClusterSummarizerBasics:
    def test_calls_model_simple_send(self):
        model = MagicMock()
        model.simple_send_with_retries.return_value = "summary result"
        summarizer = ClusterSummarizer(model)

        result = summarizer.summarize(["text A", "text B"])

        model.simple_send_with_retries.assert_called_once()
        assert result == "summary result"

    def test_passes_texts_joined_with_separator(self):
        model = MagicMock()
        model.simple_send_with_retries.return_value = "summary"
        summarizer = ClusterSummarizer(model)

        summarizer.summarize(["text A", "text B"])

        call_args = model.simple_send_with_retries.call_args[0][0]
        # User message content should contain both texts
        user_msg = [m for m in call_args if m["role"] == "user"][0]
        assert "text A" in user_msg["content"]
        assert "text B" in user_msg["content"]
        assert "---" in user_msg["content"]

    def test_includes_system_prompt(self):
        model = MagicMock()
        model.simple_send_with_retries.return_value = "summary"
        summarizer = ClusterSummarizer(model)

        summarizer.summarize(["text A"])

        call_args = model.simple_send_with_retries.call_args[0][0]
        system_msgs = [m for m in call_args if m["role"] == "system"]
        assert len(system_msgs) == 1
        assert len(system_msgs[0]["content"]) > 0


class TestClusterSummarizerCascade:
    def test_cascades_to_second_model_on_failure(self):
        model1 = MagicMock()
        model1.simple_send_with_retries.side_effect = Exception("model1 failed")
        model2 = MagicMock()
        model2.simple_send_with_retries.return_value = "fallback summary"

        summarizer = ClusterSummarizer([model1, model2])

        result = summarizer.summarize(["text"])

        model1.simple_send_with_retries.assert_called_once()
        model2.simple_send_with_retries.assert_called_once()
        assert result == "fallback summary"

    def test_cascades_on_none_return(self):
        model1 = MagicMock()
        model1.simple_send_with_retries.return_value = None
        model2 = MagicMock()
        model2.simple_send_with_retries.return_value = "got it"

        summarizer = ClusterSummarizer([model1, model2])

        result = summarizer.summarize(["text"])
        assert result == "got it"

    def test_raises_value_error_when_all_models_fail(self):
        model1 = MagicMock()
        model1.simple_send_with_retries.side_effect = Exception("fail1")
        model2 = MagicMock()
        model2.simple_send_with_retries.side_effect = Exception("fail2")

        summarizer = ClusterSummarizer([model1, model2])

        with pytest.raises(ValueError, match="unexpectedly failed"):
            summarizer.summarize(["text"])

    def test_raises_value_error_when_all_return_none(self):
        model1 = MagicMock()
        model1.simple_send_with_retries.return_value = None
        model2 = MagicMock()
        model2.simple_send_with_retries.return_value = None

        summarizer = ClusterSummarizer([model1, model2])

        with pytest.raises(ValueError, match="unexpectedly failed"):
            summarizer.summarize(["text"])


class TestClusterSummarizerSingleModel:
    def test_accepts_single_model_not_list(self):
        model = MagicMock()
        model.simple_send_with_retries.return_value = "result"
        summarizer = ClusterSummarizer(model)

        result = summarizer.summarize(["text"])
        assert result == "result"
