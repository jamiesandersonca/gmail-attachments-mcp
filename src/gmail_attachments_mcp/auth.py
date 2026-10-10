"""OAuth for a read-only Gmail token kept on this machine."""

from __future__ import annotations

import os

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CONFIG_DIR = "~/.config/gmail-attachments-mcp"
DEFAULT_CLIENT_SECRET = f"{CONFIG_DIR}/client_secret.json"
DEFAULT_TOKEN = f"{CONFIG_DIR}/token.json"


class AuthError(ValueError):
    pass


def authorize(client_secret: str, token_path: str) -> Credentials:
    """Run the browser consent flow and save the resulting token (owner-only permissions)."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_secret = os.path.expanduser(client_secret)
    if not os.path.exists(client_secret):
        raise AuthError(
            f"OAuth client file not found: {client_secret}. Create a Desktop app OAuth client in "
            "Google Cloud Console and download its JSON there (see README)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    _save(creds, token_path)
    return creds


def load_session(token_path: str) -> AuthorizedSession:
    """A requests session that signs calls with the saved token, refreshing it when needed."""
    path = os.path.expanduser(token_path)
    if not os.path.exists(path):
        raise AuthError(f"No Gmail token at {path}. Run `gmail-attachments-mcp auth` first.")
    creds = Credentials.from_authorized_user_file(path, SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise AuthError(
                f"Gmail token could not be refreshed ({e}). Run `gmail-attachments-mcp auth` again."
            ) from e
        _save(creds, token_path)
    return AuthorizedSession(creds)


class LazySession:
    """Loads the token on first use rather than at startup, so the server can run before `auth`.

    Reloads whenever the token file changes, so re-running `auth` takes effect without a restart.
    """

    def __init__(self, token_path: str):
        self.token_path = token_path
        self._session: AuthorizedSession | None = None
        self._mtime: int | None = None

    def get(self, *args, **kwargs):
        try:
            mtime = os.stat(os.path.expanduser(self.token_path)).st_mtime_ns
        except OSError:
            mtime = None
        if self._session is None or mtime != self._mtime:
            self._session = load_session(self.token_path)
            # Re-stat: loading may refresh and rewrite the token.
            self._mtime = os.stat(os.path.expanduser(self.token_path)).st_mtime_ns
        return self._session.get(*args, **kwargs)


def _save(creds: Credentials, token_path: str) -> None:
    path = os.path.expanduser(token_path)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())
    os.chmod(path, 0o600)
