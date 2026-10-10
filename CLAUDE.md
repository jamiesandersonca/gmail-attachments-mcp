# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local MCP server with a single tool, `download_attachment(messageId, attachmentId?)`, that saves
attachments from the user's live Gmail account (Gmail API, `gmail.readonly`) to local files. It
complements the Gmail connector, which can list attachments but not fetch them. Its sibling project
`gmail-takeout-mcp` has the same tool over a Takeout archive; keep the two tools' behaviour and file
handling consistent. `README.md` documents setup and user-facing behaviour; keep it in sync.

## Commands

```sh
uv sync                                         # install deps (incl. dev group: pytest)
uv run pytest                                   # all tests (no network or credentials needed)
uv run gmail-attachments-mcp auth               # OAuth consent; saves ~/.config/gmail-attachments-mcp/token.json
uv run gmail-attachments-mcp download <msgId> [--attachment <partId>]
uv run gmail-attachments-mcp serve              # MCP over stdio
```

## Architecture

- **`auth.py`**: OAuth installed-app flow and token loading/refresh. Token files are written `0600`.
- **`gmail.py`**: `Downloader` calls `messages.get` (format=full) to list attachment parts, then
  `messages.attachments.get`, or decodes inline `body.data`. Gmail issues **new attachment IDs on
  every `messages.get`**, so an ID from the connector won't match. `attachmentId` therefore accepts a
  `partId` (stable, preferred). An unmatched ID is fetched directly and mapped to a part by size,
  then by content.
- **`files.py`**: private-directory, symlink-safe, atomic file writes (copied from gmail-takeout-mcp).
- **`server.py`**: thin MCP layer; `ValueError` subclasses become `ToolError`.
- **`cli.py`**: stdout carries MCP traffic, so logging goes to stderr.

## Tests

`tests/conftest.py` provides `FakeSession`, a stand-in for the authorized requests session. It
rotates attachment IDs on each message fetch, like real Gmail. `issue_id()` simulates an ID the
connector saw earlier.
