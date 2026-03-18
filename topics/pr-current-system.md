# Aider's Context Management: Current System

## What users have today

Aider gives users three tools for managing context: `/clear`, `/drop`, and `/tokens`. That's it.

`/clear` wipes the chat history. `/drop` removes files. `/tokens` shows a breakdown of what's consuming the context window — system prompt, chat history, repo map, individual files — with token counts and costs. When the history line is large, `/tokens` helpfully suggests "use /clear to clear."

The problem is that `/clear` is a sledgehammer. You lose everything — the good context and the bad. There's no way to say "drop the 50 messages we spent debugging that import error, but keep the architectural decisions from the first 20 minutes." Users either keep everything or lose everything.

## How summarization works

When chat history exceeds `max_chat_history_tokens` (typically 1K-8K tokens, calculated as 1/16th of the model's input limit), aider automatically compresses it. The user doesn't see this happen unless they pass `--verbose`.

The algorithm is recursive. Split messages at the half-budget token boundary, summarize the older half via the weak model, recombine. If still too big, recurse (max depth 4). The output is a single user message starting with "I spoke to you previously about a number of things..." followed by an assistant "Ok."

This runs in a background thread. The user keeps chatting. When the thread finishes, aider swaps in the compressed history — but only if `done_messages` hasn't changed during compression (stale-safety). If the user's conversation moved on, the compression result is discarded and retried next turn.

The summarization prompt instructs the model to use less detail about older parts, more about recent ones, preserve filenames and function names, write in first-person user voice, and avoid code blocks.

## What users can't do

1. **See what was summarized.** The compressed history is invisible. Users don't know what the model remembers and what it's forgotten.

2. **Choose what to keep.** Summarization is automatic and indiscriminate. Important early decisions get compressed alongside trivial debugging exchanges. The split point is wherever the token count happens to land — not at a topic boundary.

3. **Inspect topics.** There's no concept of "topics" in the context. The history is a flat list of messages. After summarization, it's an opaque text blob plus recent messages.

4. **Recover compressed details.** Once messages are summarized, the originals are gone from the active context. The raw history exists in `.aider.chat.history.md`, but there's no command to pull specific details back in.

5. **Manage context across sessions.** `--restore-chat-history` loads the previous session's messages and immediately summarizes them. There's no structured knowledge that persists — just a raw message dump that gets compressed on load.

## What users are asking for

Issue #3607 (10 comments, open) captures it directly: "important early conversations get summarized away while unimportant recent ones consume context." One user reports that "messy context confuses the LLM." Another says they want checkbox-based selection of which history to keep.

Issue #2219 asks to see and edit the exact context being sent, calling the current lack of visibility "wasting a lot of my time."

Issue #948 requests a table of context consumers with token counts and selective dropping — like `/tokens` but actionable.

Issue #4079 wants named session archives, arguing that "chat history serves as storage for the theory of the codebase."

The common thread: users want to see what's in their context, organized by topic, and selectively manage it. Not a sledgehammer `/clear`. Not invisible automatic compression. Something in between.

## The gap

Aider's context management is algorithmically sound (recursive compression, background threading, model cascade, stale-safety) but user-facing primitive. The summarizer treats context as a flat token stream. Users experience context as topics — "the auth refactor," "the database migration," "that import error we fixed." There's no bridge between how users think about their conversation and how aider manages it.

The `/tokens` command proves the pattern works: show users what's consuming their context, let them act on it. But `/tokens` operates at the file level. There's no equivalent for chat history.
