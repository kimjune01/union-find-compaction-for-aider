"""Tests for ChatSummaryUF — drop-in replacement for ChatSummary."""

from unittest.mock import MagicMock, patch, PropertyMock
import sys
import os
import pytest

# We need to mock aider modules before importing chat_summary_uf
# since it imports from aider.history and aider.prompts

# Create mock aider modules
mock_prompts = MagicMock()
mock_prompts.summary_prefix = "I spoke to you previously about a number of things.\n"
mock_prompts.summarize = "Summarize this conversation..."

mock_models_module = MagicMock()
mock_dump = MagicMock()

# Mock aider.history.ChatSummary
class MockChatSummary:
    """Minimal mock of aider's ChatSummary for testing."""

    def __init__(self, models=None, max_tokens=1024):
        if not models:
            raise ValueError("At least one model must be provided")
        self.models = models if isinstance(models, list) else [models]
        self.max_tokens = max_tokens
        self.token_count = self.models[0].token_count

    def too_big(self, messages):
        total = sum(self.token_count(m) for m in messages)
        return total > self.max_tokens

    def summarize(self, messages, depth=0):
        # Simplified: just return a summary message
        content = mock_prompts.summary_prefix + "recursive summary"
        result = [
            {"role": "user", "content": content},
            {"role": "assistant", "content": "Ok."},
        ]
        return result

    def summarize_all(self, messages):
        content = mock_prompts.summary_prefix + "summarize_all result"
        return [{"role": "user", "content": content}]


# Patch aider modules in sys.modules before import
sys.modules["aider"] = MagicMock()
sys.modules["aider.history"] = MagicMock(ChatSummary=MockChatSummary)
sys.modules["aider.prompts"] = mock_prompts
sys.modules["aider.models"] = mock_models_module
sys.modules["aider.dump"] = mock_dump

from chat_summary_uf import ChatSummaryUF


def _make_model(token_count_fn=None):
    """Create a mock model with token_count and simple_send_with_retries."""
    model = MagicMock()
    if token_count_fn is None:
        # Default: ~10 tokens per message
        model.token_count.side_effect = lambda msg: len(msg.get("content", "")) // 4 + 1
    else:
        model.token_count.side_effect = token_count_fn
    model.simple_send_with_retries.return_value = "cluster summary"
    model.info = {"max_input_tokens": 4096}
    model.name = "mock-model"
    return model


def _make_messages(n, content_prefix="Message"):
    """Create n alternating user/assistant messages."""
    messages = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"{content_prefix} {i}"})
    return messages


class TestChatSummaryUFSubclass:
    def test_is_subclass_of_chat_summary(self):
        model = _make_model()
        uf = ChatSummaryUF(models=model)
        assert isinstance(uf, MockChatSummary)

    def test_constructor_accepts_same_args(self):
        model = _make_model()
        uf = ChatSummaryUF(models=model, max_tokens=2048)
        assert uf.max_tokens == 2048


class TestChatSummaryUFOutputFormat:
    def test_output_is_list_of_dicts(self):
        model = _make_model()
        uf = ChatSummaryUF(models=model, max_tokens=50)

        messages = _make_messages(40)
        result = uf.summarize(messages)

        assert isinstance(result, list)
        for msg in result:
            assert isinstance(msg, dict)
            assert "role" in msg
            assert "content" in msg

    def test_output_ends_with_assistant(self):
        model = _make_model()
        uf = ChatSummaryUF(models=model, max_tokens=50)

        messages = _make_messages(40)
        result = uf.summarize(messages)

        assert result[-1]["role"] == "assistant"

    def test_output_starts_with_summary_prefix(self):
        """Cold cluster output should start with aider's summary_prefix."""
        model = _make_model()
        uf = ChatSummaryUF(models=model, max_tokens=50)

        messages = _make_messages(40)
        result = uf.summarize(messages)

        # First message should be the summary (user role with prefix)
        assert result[0]["role"] == "user"
        assert result[0]["content"].startswith(mock_prompts.summary_prefix)


class TestChatSummaryUFBudgetCompliance:
    def test_result_smaller_than_input(self):
        model = _make_model()
        uf = ChatSummaryUF(models=model, max_tokens=50)

        messages = _make_messages(40)
        result = uf.summarize(messages)

        input_tokens = sum(model.token_count(m) for m in messages)
        result_tokens = sum(model.token_count(m) for m in result)
        assert result_tokens < input_tokens

    def test_falls_back_to_recursive_on_inflation(self):
        """If union-find output is >= input tokens, fall back to recursive."""
        model = _make_model()
        # Make cluster summaries very long (inflated)
        model.simple_send_with_retries.return_value = "x" * 10000

        uf = ChatSummaryUF(models=model, max_tokens=50)
        messages = _make_messages(40)

        result = uf.summarize(messages)

        # Should have fallen back to recursive (MockChatSummary.summarize)
        assert result[0]["content"].startswith(mock_prompts.summary_prefix)

    def test_falls_back_when_exceeds_max_tokens(self):
        """If result tokens > max_tokens, fall back to recursive."""
        model = _make_model()
        # Token count returns a large number for summary messages
        original_tc = model.token_count.side_effect

        def inflated_tc(msg):
            content = msg.get("content", "")
            if "cluster summary" in content or "I spoke to you" in content:
                return 9999  # Way over budget
            return len(content) // 4 + 1

        model.token_count.side_effect = inflated_tc

        uf = ChatSummaryUF(models=model, max_tokens=50)
        messages = _make_messages(40)

        result = uf.summarize(messages)

        # Should be recursive fallback
        assert any("recursive summary" in m.get("content", "") for m in result) or \
               any("summarize_all" in m.get("content", "") for m in result)


class TestChatSummaryUFCompressionHole:
    def test_falls_back_when_no_cold_clusters(self):
        """<27 large messages that exceed budget but don't form cold clusters
        should fall back to super().summarize(), not return unchanged."""
        model = _make_model()
        # Make each message very expensive token-wise
        model.token_count.side_effect = lambda msg: 100  # 100 tokens each

        uf = ChatSummaryUF(models=model, max_tokens=500)

        # 10 messages × 100 tokens = 1000 > 500 budget, but < 27 so no graduation
        messages = _make_messages(10)
        result = uf.summarize(messages)

        # Should NOT return the original messages unchanged
        assert len(result) != len(messages)
        # Should be the recursive fallback
        assert result[0]["content"].startswith(mock_prompts.summary_prefix)


class TestChatSummaryUFIncrementalFeeding:
    def test_feeds_only_new_messages(self):
        model = _make_model()
        uf = ChatSummaryUF(models=model, max_tokens=50)

        messages_10 = _make_messages(10)
        messages_20 = _make_messages(20)

        # First call feeds 10
        uf.summarize(messages_10)

        # Second call with 20 should only feed the new 10
        # We verify by checking _fed_count
        uf.summarize(messages_20)
        assert uf._fed_count == 20

    def test_incremental_feeding_preserves_forest(self):
        model = _make_model()
        uf = ChatSummaryUF(models=model, max_tokens=50)

        messages_30 = _make_messages(30)

        # First call
        uf.summarize(messages_30)
        cold_count_1 = uf.context_window.cold_count

        messages_40 = _make_messages(40)

        # Second call — forest should persist and grow
        uf.summarize(messages_40)
        cold_count_2 = uf.context_window.cold_count

        # More messages should mean at least as many clusters
        assert cold_count_2 >= cold_count_1


class TestChatSummaryUFStaleDetection:
    def test_rebuild_on_message_shrinkage(self):
        """When messages shrink (previous result was applied), rebuild forest."""
        model = _make_model()
        uf = ChatSummaryUF(models=model, max_tokens=50)

        messages_40 = _make_messages(40)
        uf.summarize(messages_40)
        assert uf._fed_count == 40

        # Simulate result being applied: messages shrank but still over budget
        messages_20 = _make_messages(20, content_prefix="New replacement message with enough content to exceed budget")
        uf.summarize(messages_20)

        # _fed_count should have reset (rebuild) then set to 20
        assert uf._fed_count == 20

    def test_no_rebuild_on_message_growth(self):
        """When messages grow (previous result was discarded), feed delta."""
        model = _make_model()
        uf = ChatSummaryUF(models=model, max_tokens=50)

        messages_30 = _make_messages(30)
        uf.summarize(messages_30)

        # Messages grew — previous result was discarded, user added more
        messages_35 = _make_messages(35)
        uf.summarize(messages_35)

        # Should NOT have rebuilt, _fed_count should be 35
        assert uf._fed_count == 35


class TestChatSummaryUFSummarizeAll:
    def test_delegates_to_parent(self):
        model = _make_model()
        uf = ChatSummaryUF(models=model)

        messages = _make_messages(10)
        result = uf.summarize_all(messages)

        # Should be the parent's summarize_all result
        assert len(result) == 1
        assert "summarize_all result" in result[0]["content"]


class TestChatSummaryUFNotTooBig:
    def test_returns_messages_unchanged_when_within_budget(self):
        model = _make_model()
        uf = ChatSummaryUF(models=model, max_tokens=99999)

        messages = _make_messages(5)
        result = uf.summarize(messages)

        assert result == messages
