"""Tests for question generator."""

import json

import pytest

from question_generator import QuestionGenerator
from schemas import Conversation, RecallQuestion


def _valid_questions_json():
    return json.dumps([
        {"id": "q1", "question": "What file?", "expected_answer": "models.py", "category": "filename", "region": "early"},
        {"id": "q2", "question": "What function?", "expected_answer": "get_queryset", "category": "function_name", "region": "early"},
        {"id": "q3", "question": "What library?", "expected_answer": "django.db", "category": "library", "region": "mid"},
        {"id": "q4", "question": "What error?", "expected_answer": "IntegrityError", "category": "error_message", "region": "mid"},
        {"id": "q5", "question": "What decision?", "expected_answer": "Use raw SQL", "category": "decision", "region": "mid"},
        {"id": "q6", "question": "What config?", "expected_answer": "DEBUG=True", "category": "configuration", "region": "late"},
        {"id": "q7", "question": "What endpoint?", "expected_answer": "/api/users", "category": "api_endpoint", "region": "late"},
        {"id": "q8", "question": "What test?", "expected_answer": "test_query_join", "category": "test_case", "region": "late"},
    ])


class TestQuestionGenerator:
    def test_parse_valid(self):
        qs = QuestionGenerator().parse_questions(_valid_questions_json(), "c1")
        assert len(qs) == 8

    def test_parse_code_fences(self):
        qs = QuestionGenerator().parse_questions(f"```json\n{_valid_questions_json()}\n```", "c1")
        assert len(qs) == 8

    def test_parse_invalid(self):
        with pytest.raises(ValueError):
            QuestionGenerator().parse_questions("not json", "c1")

    def test_validate_count(self):
        gen = QuestionGenerator()
        qs = gen.parse_questions(_valid_questions_json(), "c1")
        assert gen.validate_count(qs)
        assert not gen.validate_count(qs[:5])

    def test_validate_categories(self):
        gen = QuestionGenerator()
        qs = gen.parse_questions(_valid_questions_json(), "c1")
        assert gen.validate_categories(qs)

    def test_validate_categories_fails(self):
        gen = QuestionGenerator()
        same = [RecallQuestion(f"q{i}", "c1", "Q?", "A", "filename", r) for i, r in enumerate(["early", "mid", "late"])]
        assert not gen.validate_categories(same)

    def test_validate_regions(self):
        gen = QuestionGenerator()
        qs = gen.parse_questions(_valid_questions_json(), "c1")
        assert gen.validate_regions(qs)

    def test_validate_regions_fails(self):
        gen = QuestionGenerator()
        no_late = [RecallQuestion(f"q{i}", "c1", "Q?", "A", "filename", "early") for i in range(4)]
        assert not gen.validate_regions(no_late)

    def test_build_prompt(self):
        conv = Conversation("c1", "t/t", 1, "Bug", [{"role": "user", "content": "msg"}])
        prompt = QuestionGenerator().build_prompt(conv)
        assert "msg" in prompt
