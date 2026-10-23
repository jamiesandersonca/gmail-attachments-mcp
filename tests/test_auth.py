import os
import stat
import sys

import pytest
from google.oauth2.credentials import Credentials

from gmail_attachments_mcp import auth
from gmail_attachments_mcp.auth import AuthError, LazySession
from gmail_attachments_mcp.files import is_quarantined
from gmail_attachments_mcp.gmail import Downloader
from gmail_attachments_mcp.server import build_server

from .test_server import _call, _text


def test_server_starts_without_a_token_and_the_tool_says_how_to_fix_it(tmp_path):
    downloader = Downloader(LazySession(str(tmp_path / "token.json")), str(tmp_path / "out"))
    build_server(downloader)  # no token needed to build or list tools
    result = _call(downloader, "download_attachment", {"messageId": "1a0000000000000a"})
    assert result.is_error and "Run `gmail-attachments-mcp auth` first" in _text(result)


def test_session_loads_once_and_reloads_when_the_token_changes(tmp_path, monkeypatch, session):
    token = tmp_path / "token.json"
    loads = []

    def fake_load(path):
        loads.append(path)
        return session

    monkeypatch.setattr(auth, "load_session", fake_load)
    lazy = LazySession(str(token))
    token.write_text("{}")
    lazy.get("https://example/a")
    lazy.get("https://example/b")
    assert len(loads) == 1

    os.utime(token, ns=(0, os.stat(token).st_mtime_ns + 1_000_000))  # `auth` was run again
    lazy.get("https://example/c")
    assert len(loads) == 2 and session.calls == ["https://example/a", "https://example/b",
                                                 "https://example/c"]


def test_missing_token_is_an_auth_error(tmp_path):
    with pytest.raises(AuthError, match="auth"):
        LazySession(str(tmp_path / "token.json")).get("https://example")


class _Creds:
    def to_json(self):
        return '{"token": "new"}'


def test_token_is_saved_owner_only_without_quarantine(tmp_path):
    token = tmp_path / "config" / "token.json"
    auth._save(_Creds(), str(token))
    assert token.read_text() == '{"token": "new"}'
    assert stat.S_IMODE(os.stat(token).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(token.parent).st_mode) == 0o700
    fd = os.open(token, os.O_RDONLY)
    try:
        assert sys.platform != "darwin" or not is_quarantined(fd)
    finally:
        os.close(fd)


def test_token_save_replaces_a_symlink_instead_of_following_it(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("precious")
    token = tmp_path / "token.json"
    token.symlink_to(victim)
    auth._save(_Creds(), str(token))
    assert victim.read_text() == "precious"
    assert not token.is_symlink() and token.read_text() == '{"token": "new"}'
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


class _Flow:
    def __init__(self, refresh_token):
        self.refresh_token = refresh_token
        self.kwargs = None

    def run_local_server(self, **kwargs):
        self.kwargs = kwargs
        return Credentials(token="access", refresh_token=self.refresh_token, client_id="id",
                           client_secret="secret", token_uri="https://oauth2.googleapis.com/token")


@pytest.fixture
def flow(monkeypatch):
    from google_auth_oauthlib.flow import InstalledAppFlow

    def use(refresh_token):
        f = _Flow(refresh_token)
        monkeypatch.setattr(InstalledAppFlow, "from_client_secrets_file",
                            classmethod(lambda cls, path, scopes: f))
        return f

    return use


def test_auth_asks_for_consent_so_a_refresh_token_is_issued(tmp_path, flow):
    secret = tmp_path / "client_secret.json"
    secret.write_text("{}")
    f = flow("refresh")
    auth.authorize(str(secret), str(tmp_path / "token.json"))
    assert f.kwargs["prompt"] == "consent"
    assert '"refresh_token": "refresh"' in (tmp_path / "token.json").read_text()


def test_token_without_a_refresh_token_is_not_saved(tmp_path, flow):
    secret = tmp_path / "client_secret.json"
    secret.write_text("{}")
    flow(None)
    with pytest.raises(AuthError, match="didn't return a refresh token"):
        auth.authorize(str(secret), str(tmp_path / "token.json"))
    assert not (tmp_path / "token.json").exists()
