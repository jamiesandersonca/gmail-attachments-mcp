# gmail-attachments-mcp

A local MCP server with one tool, `download_attachment`. It saves attachments from your live
Gmail account to local files. The Gmail connector can search mail and list attachments, but it
can't fetch them. This server fills that gap, and you keep using the connector for everything
else.

It is the live-mail counterpart of
[gmail-takeout-mcp](https://github.com/jamiesandersonca/gmail-takeout-mcp)'s `download_attachment`,
and it saves files the same way.

## How it fits with the connector

1. `search_threads` (connector), e.g. `has:attachment filename:pdf from:hotel`
2. `get_message` (connector) returns `attachments`, each with a `partId` and an `id`
3. `download_attachment(messageId, attachmentId)` (this server) saves the file and returns its `path`

Message IDs are the same hex IDs the connector returns. The decimal form also works.

`attachmentId` accepts either:

- **`partId`** (e.g. `"1"`). This is preferred, because it is stable.
- **the attachment `id`** from the connector. Gmail issues a new attachment ID every time a message
  is fetched, so the connector's ID won't match the one this server sees. The ID still downloads,
  and the server works out which part it is from the size, comparing contents if needed.

Omit `attachmentId` to save every attachment of the message.

Files are saved to `~/.cache/gmail-attachments-mcp/attachments/<messageId>/` with owner-only
permissions. You can change the location with `--attachments-dir`. Files keep their original
names, unless two names in the message clash ignoring case or a name is missing. In those cases
the name becomes `part<partId>-<name>` or `part<partId>.<ext>`. Repeat requests reuse the saved
file when its contents still match.

## Setup

You need a Google Cloud OAuth client. This is a one-time setup:

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project (or reuse one)
   and enable the **Gmail API**.
2. Under **Google Auth Platform → Audience**, choose **External** and add your Gmail address as a
   **test user**.
3. Under **Clients**, create a client with the **Desktop app** type and download its JSON to
   `~/.config/gmail-attachments-mcp/client_secret.json`.
4. Authorize with the **same account the Gmail connector uses**:

   ```sh
   uv sync
   uv run gmail-attachments-mcp auth
   ```

   This opens a browser for consent and requests the `gmail.readonly` scope. The token is saved
   to `~/.config/gmail-attachments-mcp/token.json` with `0600` permissions.

While the OAuth app is in **testing** status, Google expires refresh tokens after 7 days. When
calls start failing with an auth error, run `auth` again, or publish the app to stop the expiry.

`serve` starts without a token, so you can register the server before authorizing. Until you
authorize, the tool returns an error telling you to run `auth`. The server re-reads the token
whenever the file changes, so re-running `auth` takes effect without a restart.

## Use with Claude Code

```sh
claude mcp add gmail-attachments --scope user -- \
  uv --directory /path/to/gmail-attachments-mcp run gmail-attachments-mcp serve
```

## CLI

```sh
uv run gmail-attachments-mcp download 1a11b81ba2900752 [--attachment 1]
```

## Security notes

- The token can read all of your mail. It lives only on this machine, and the server never
  writes to Gmail.
- Attachment names and contents come from the sender, so treat them as untrusted. The server's
  MCP instructions tell the model the same. Filenames are reduced to safe basenames, and writes
  never follow symlinks planted in the attachments directory.

## Development

```sh
uv run pytest
```

Tests use a fake Gmail API session (`tests/conftest.py`). This fake issues a fresh attachment ID
on every message fetch, the way Gmail does, so no network access or credentials are needed.
