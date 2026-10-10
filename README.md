# gmail-attachments-mcp

A local MCP server with one tool, `download_attachment`. It saves attachments from your live
Gmail account to local files. The Gmail connector can search mail and list attachments, but it
can't fetch them. This server fills that gap, and you keep using the connector for everything
else.

It is the live-mail counterpart of the `download_attachment` tool in gmail-takeout-mcp (a
companion server for Google Takeout archives), and it saves files the same way.

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
   and enable the **Gmail API**. For a personal Gmail account, choose **No organisation** as the
   project's parent.
2. Under **Google Auth Platform**, click **Get started** and set up the consent screen. Choose
   **External** for the audience. **Internal** is only for Google Workspace organisations and
   won't accept a @gmail.com account.
3. Under **Audience → Test users**, add your Gmail address. The setup flow doesn't ask for this.
   Without it, sign-in fails with "Access blocked: … has not completed the Google verification
   process".
4. Under **Clients**, create a client with the **Desktop app** type and tick **Use this client
   for an AI-powered agent**. Download its JSON to
   `~/.config/gmail-attachments-mcp/client_secret.json`.
5. Authorize with the **same account the Gmail connector uses**:

   ```sh
   uv sync
   uv run gmail-attachments-mcp auth
   ```

   This opens a browser for consent and requests the `gmail.readonly` scope. Google warns that it
   "hasn't verified this app". Since this is your own app, click **Advanced → Go to … (unsafe)**
   and approve. The token is saved to `~/.config/gmail-attachments-mcp/token.json` with `0600`
   permissions.

While the OAuth app is in **Testing** status, Google expires refresh tokens after 7 days. When
calls start failing with an auth error, run `auth` again, or publish the app (below) to stop the
expiry.

`serve` starts without a token, so you can register the server before authorizing. Until you
authorize, the tool returns an error telling you to run `auth`. The server re-reads the token
whenever the file changes, so re-running `auth` takes effect without a restart.

### Publishing the app (optional)

A published app's refresh tokens don't expire after 7 days. They last until you revoke access,
change your Google password, or leave the token unused for 6 months. You don't need Google's
verification for personal use: an unverified app can have up to 100 users and only shows the
"unverified app" warning at sign-in.

Publishing requires a home page, privacy policy and terms of service on a domain you've verified.
This repo's `docs/` provides them (see [Project site](#project-site)). To use your own copy:

1. Enable GitHub Pages for your fork from `main` → `/docs`. On GitHub's free plan the repo must be
   public.
2. In [Google Search Console](https://search.google.com/search-console), add the
   **URL prefix** property `https://<user>.github.io/`. Choose **HTML file** verification and
   serve that file from the root of your `<user>.github.io` repo. A project site can't serve the
   root, so you need that repo even if it holds nothing else. Keep the file there, because Google
   re-checks it.
3. Under **Branding → App domain**, enter the home page, privacy policy and terms URLs under
   `https://<user>.github.io/gmail-attachments-mcp/`. Add `<user>.github.io` as an
   **authorised domain** and save.
4. Under **Audience**, click **Publish app**.
5. Run `auth` again. Tokens issued while the app was in Testing still expire.

## Use with Claude Code

Run it from a checkout:

```sh
claude mcp add gmail-attachments --scope user -- \
  uv --directory /path/to/gmail-attachments-mcp run gmail-attachments-mcp serve
```

Or install it as a tool, and reinstall with `--reinstall` after pulling changes:

```sh
uv tool install /path/to/gmail-attachments-mcp
claude mcp add gmail-attachments --scope user -- gmail-attachments-mcp serve
```

## CLI

```sh
uv run gmail-attachments-mcp download 1a0000000000000a [--attachment 1]
```

## Security notes

- The token can read all of your mail. It lives only on this machine, and the server never
  writes to Gmail.
- Attachment names and contents come from the sender, so treat them as untrusted. The server's
  MCP instructions tell the model the same. Filenames are reduced to safe basenames, and writes
  never follow symlinks planted in the attachments directory.

## Project site

`docs/` is published with GitHub Pages at
<https://jamiesandersonca.github.io/gmail-attachments-mcp/>. It holds the home page, privacy policy
and terms that the Google OAuth consent screen links to.

## Development

```sh
uv run pytest
```

Tests use a fake Gmail API session (`tests/conftest.py`). This fake issues a fresh attachment ID
on every message fetch, the way Gmail does, so no network access or credentials are needed.
