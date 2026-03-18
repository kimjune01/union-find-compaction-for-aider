"""Prompt templates for experiment pipeline."""

EXPAND_ISSUE_SYSTEM = """\
You are simulating a realistic, multi-turn coding conversation between a \
developer (user) and an AI coding assistant (assistant). The conversation \
should feel natural and cover iterative debugging, code changes, and \
problem-solving.

Rules:
- Alternate between user and assistant roles strictly.
- Each message should be 50-200 words.
- Include realistic code references: filenames, function names, class names, \
  error messages, stack traces, and library imports.
- Include at least 5 distinct phases of work (investigation, hypothesis, \
  fix attempt, testing, refinement).
- Reference specific filenames from the project's codebase.
- Do NOT wrap code in markdown code blocks — use inline references instead.
- Make decisions that a real developer would make (choosing libraries, \
  config values, API patterns).
"""

EXPAND_ISSUE_CHUNK = """\
Continue the conversation from message {start} to message {end} \
(of {total} total messages). This is chunk {chunk}/{total_chunks}.

GitHub Issue Context:
Repository: {repo}
Issue #{issue_number}: {title}

{body}

Previous messages so far:
{previous_messages}

Generate exactly {count} messages continuing this conversation, alternating \
user/assistant roles starting with {next_role}. Output as JSON array:
[{{"role": "user", "content": "..."}}, {{"role": "assistant", "content": "..."}}]
"""

GENERATE_QUESTIONS = """\
You are generating factual recall questions about a coding conversation. \
Each question must have a single, objective, factual answer that appears \
explicitly in the conversation.

Given the following conversation, generate exactly 8 recall questions.

Requirements:
- Each question must reference a specific detail: a filename, function name, \
  library, error message, decision, configuration value, API endpoint, or \
  test case.
- Use at least 3 different categories from: filename, function_name, library, \
  error_message, decision, configuration, api_endpoint, test_case.
- Questions must span different regions of the conversation:
  - At least 2 from early (first third)
  - At least 2 from mid (middle third)
  - At least 2 from late (final third)
- Answers must be short (1-10 words) and unambiguous.

Conversation:
{conversation}

Output as JSON array:
[{{"id": "q1", "question": "...", "expected_answer": "...", \
"category": "...", "region": "early|mid|late"}}]
"""

JUDGE_PROMPT = """\
You are evaluating whether a compressed conversation history retains a \
specific factual detail from the original conversation.

You will be given:
1. A compressed version of a coding conversation
2. A factual question about the original conversation
3. The expected correct answer

Your task: Determine if the compressed history contains enough information \
to correctly answer the question.

Rules:
- Answer CORRECT if the compressed history contains or strongly implies the \
  expected answer.
- Answer INCORRECT if the expected answer cannot be determined from the \
  compressed history.
- Consider semantic equivalence (e.g., "django.db.models" matches \
  "the models module in Django's DB layer").
- Be strict: vague references without the specific detail count as INCORRECT.

Compressed History:
{compressed_history}

Question: {question}
Expected Answer: {expected_answer}

First provide brief reasoning (2-3 sentences), then on the final line write \
exactly one word: CORRECT or INCORRECT
"""
