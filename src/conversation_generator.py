"""GitHub issues -> 200-message synthetic conversations via Gemini Flash."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

import litellm

from prompts_experiment import EXPAND_ISSUE_CHUNK, EXPAND_ISSUE_SYSTEM
from schemas import Conversation
from token_tracker import TokenTracker, LLMCallRecord


class ConversationGenerator:
    """Fetches GitHub issues and expands them into synthetic conversations."""

    def __init__(self, model_name: str = "gemini/gemini-3.1-flash-lite-preview",
                 tracker: TokenTracker | None = None,
                 model=None) -> None:
        self._model_name = model_name
        self._model = model  # kept for backward compat with tests
        self._tracker = tracker or TokenTracker()

    def parse_issue(self, repo: str, issue: dict[str, Any]) -> dict[str, Any]:
        return {
            "repo": repo,
            "issue_number": issue["number"],
            "title": issue["title"],
            "body": issue.get("body", ""),
        }

    def build_conversation_id(self, repo: str, issue_number: int) -> str:
        return f"{repo.replace('/', '-')}-{issue_number}"

    def chunk_plan(
        self, total_messages: int = 200, n_chunks: int = 5
    ) -> list[dict[str, Any]]:
        base_count = total_messages // n_chunks
        remainder = total_messages % n_chunks
        chunks = []
        cursor = 1
        for i in range(n_chunks):
            count = base_count + (1 if i < remainder else 0)
            chunks.append({
                "start": cursor,
                "end": cursor + count - 1,
                "count": count,
                "chunk": i + 1,
                "total_chunks": n_chunks,
                "next_role": "user" if (cursor - 1) % 2 == 0 else "assistant",
            })
            cursor += count
        return chunks

    def parse_messages(self, raw: str) -> list[dict[str, str]]:
        text = raw.strip()
        match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()
        try:
            messages = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse messages JSON: {e}") from e
        if not isinstance(messages, list):
            raise ValueError("Expected JSON array of messages")
        return messages

    def validate_messages(self, messages: list[dict[str, str]]) -> bool:
        for i in range(1, len(messages)):
            if messages[i]["role"] == messages[i - 1]["role"]:
                return False
        return True

    def fetch_issues(
        self, repo: str, limit: int = 6, min_comments: int = 15,
    ) -> list[dict[str, Any]]:
        """Fetch issues (not PRs) from GitHub via gh CLI."""
        cmd = [
            "gh", "api",
            f"repos/{repo}/issues?state=closed&sort=comments&direction=desc&per_page={limit * 3}",
            "-q",
            f'[.[] | select(.comments >= {min_comments} and .pull_request.url == null)] | .[:{limit}]',
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"gh api failed: {result.stderr}")
        issues = json.loads(result.stdout)
        return [self.parse_issue(repo, issue) for issue in issues]

    def build_expand_prompt(
        self, issue: dict[str, Any], chunk: dict[str, Any],
        previous_messages: list[dict[str, str]],
    ) -> str:
        prev_text = ""
        if previous_messages:
            prev_text = "\n".join(
                f"{m['role']}: {m['content']}" for m in previous_messages[-10:]
            )
        return EXPAND_ISSUE_CHUNK.format(
            start=chunk["start"], end=chunk["end"],
            total=chunk["start"] + chunk["count"] - 1,
            chunk=chunk["chunk"], total_chunks=chunk["total_chunks"],
            repo=issue["repo"], issue_number=issue["issue_number"],
            title=issue["title"], body=issue.get("body", "")[:2000],
            previous_messages=prev_text or "(Start of conversation)",
            count=chunk["count"], next_role=chunk["next_role"],
        )

    def _call_llm(self, messages: list[dict[str, str]], max_tokens: int = 8192) -> str:
        """Call LLM via litellm directly for reliable long output."""
        resp = litellm.completion(
            model=self._model_name,
            messages=messages,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        # Track tokens
        usage = resp.usage
        self._tracker.record(LLMCallRecord(
            system="generation", role="conversation_generation",
            prompt_tokens=usage.prompt_tokens or 0,
            completion_tokens=usage.completion_tokens or 0,
            model=self._model_name,
        ))
        return content

    def generate_conversation(
        self, issue: dict[str, Any],
        total_messages: int = 200, n_chunks: int = 10,
        max_retries: int = 3,
    ) -> Conversation:
        """Generate a full conversation from an issue using chunked LLM calls."""
        chunks = self.chunk_plan(total_messages, n_chunks)
        all_messages: list[dict[str, str]] = []

        for chunk in chunks:
            prompt = self.build_expand_prompt(issue, chunk, all_messages)
            messages = [
                {"role": "system", "content": EXPAND_ISSUE_SYSTEM},
                {"role": "user", "content": prompt},
            ]
            for attempt in range(max_retries):
                raw = self._call_llm(messages)
                try:
                    parsed = self.parse_messages(raw)
                    all_messages.extend(parsed)
                    break
                except ValueError:
                    if attempt == max_retries - 1:
                        raise

        conv_id = self.build_conversation_id(issue["repo"], issue["issue_number"])
        return Conversation(
            id=conv_id, repo=issue["repo"],
            issue_number=issue["issue_number"], title=issue["title"],
            messages=all_messages,
        )
