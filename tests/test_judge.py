"""Tests for blinded judge via codex exec."""

from unittest.mock import MagicMock, patch

from judge import BlindedJudge
from schemas import RecallQuestion


class TestBlindedJudge:
    def test_randomizes_ab_per_conversation(self):
        judge = BlindedJudge(seed=42)
        a_values = [judge.get_ab_assignment(f"conv-{i}")[0] for i in range(100)]
        assert len(set(a_values)) == 2

    def test_deterministic_for_same_conv(self):
        judge = BlindedJudge(seed=42)
        assert judge.get_ab_assignment("conv-1") == judge.get_ab_assignment("conv-1")

    def test_parses_correct(self):
        judge = BlindedJudge(seed=42)
        mock_result = MagicMock(returncode=0, stdout="Found it.\nCORRECT")
        with patch("subprocess.run", return_value=mock_result):
            verdict, _ = judge.judge_question("history", "Q?", "A")
        assert verdict == "CORRECT"

    def test_parses_incorrect(self):
        judge = BlindedJudge(seed=42)
        mock_result = MagicMock(returncode=0, stdout="Not found.\nINCORRECT")
        with patch("subprocess.run", return_value=mock_result):
            verdict, _ = judge.judge_question("history", "Q?", "A")
        assert verdict == "INCORRECT"

    def test_defaults_on_parse_failure(self):
        judge = BlindedJudge(seed=42)
        mock_result = MagicMock(returncode=0, stdout="I'm not sure.")
        with patch("subprocess.run", return_value=mock_result):
            verdict, _ = judge.judge_question("history", "Q?", "A")
        assert verdict == "INCORRECT"

    def test_defaults_on_error(self):
        judge = BlindedJudge(seed=42)
        mock_result = MagicMock(returncode=1, stdout="", stderr="Error")
        with patch("subprocess.run", return_value=mock_result):
            verdict, _ = judge.judge_question("history", "Q?", "A")
        assert verdict == "INCORRECT"

    def test_judge_conversation_returns_judgments(self):
        judge = BlindedJudge(seed=42)
        qs = [RecallQuestion("q1", "c1", "Q?", "A", "filename", "early"),
              RecallQuestion("q2", "c1", "Q?", "A", "filename", "mid")]
        mock_result = MagicMock(returncode=0, stdout="Found.\nCORRECT")
        with patch("subprocess.run", return_value=mock_result):
            judgments = judge.judge_conversation("c1", qs, {"union_find": "uf", "recursive": "rec"})
        assert len(judgments) == 4
        assert {j.system for j in judgments} == {"union_find", "recursive"}

    def test_codex_command_includes_gpt54(self):
        judge = BlindedJudge(seed=42)
        mock_result = MagicMock(returncode=0, stdout="CORRECT")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            judge.judge_question("history", "Q?", "A")
        cmd_str = " ".join(mock_run.call_args[0][0])
        assert "codex" in cmd_str and "gpt-5.4" in cmd_str
