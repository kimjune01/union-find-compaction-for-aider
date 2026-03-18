"""Tests for conversation generator."""

import json
from unittest.mock import MagicMock, patch

import pytest

from conversation_generator import ConversationGenerator


class TestConversationGenerator:
    def test_parse_issue(self):
        gen = ConversationGenerator()
        parsed = gen.parse_issue("pallets/flask", {"number": 1361, "title": "Bug", "body": "desc", "comments": 20})
        assert parsed["repo"] == "pallets/flask"
        assert parsed["issue_number"] == 1361

    def test_build_conversation_id(self):
        assert ConversationGenerator().build_conversation_id("pallets/flask", 1361) == "pallets-flask-1361"

    def test_chunk_plan_counts(self):
        chunks = ConversationGenerator().chunk_plan(200, 5)
        assert len(chunks) == 5
        assert sum(c["count"] for c in chunks) == 200

    def test_chunk_plan_uneven(self):
        chunks = ConversationGenerator().chunk_plan(43, 5)
        assert sum(c["count"] for c in chunks) == 43

    def test_parse_messages(self):
        raw = json.dumps([{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}])
        msgs = ConversationGenerator().parse_messages(raw)
        assert len(msgs) == 2

    def test_parse_messages_code_fences(self):
        raw = '```json\n[{"role": "user", "content": "Hi"}]\n```'
        assert len(ConversationGenerator().parse_messages(raw)) == 1

    def test_parse_messages_invalid(self):
        with pytest.raises(ValueError):
            ConversationGenerator().parse_messages("not json")

    def test_validate_messages(self):
        gen = ConversationGenerator()
        assert gen.validate_messages([{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])
        assert not gen.validate_messages([{"role": "user", "content": "a"}, {"role": "user", "content": "b"}])

    def test_fetch_issues(self):
        gen = ConversationGenerator()
        mock_result = MagicMock(returncode=0, stdout=json.dumps([
            {"number": 1, "title": "Bug", "body": "desc", "comments": 20},
        ]))
        with patch("subprocess.run", return_value=mock_result):
            issues = gen.fetch_issues("pallets/flask", limit=1)
        assert len(issues) == 1

    def test_build_expand_prompt(self):
        gen = ConversationGenerator()
        prompt = gen.build_expand_prompt(
            {"repo": "pallets/flask", "issue_number": 1361, "title": "Bug", "body": "desc"},
            {"start": 1, "end": 40, "count": 40, "chunk": 1, "total_chunks": 5, "next_role": "user"},
            [],
        )
        assert "pallets/flask" in prompt
