import os

import pytest

from gmail_attachments_mcp import auth
from gmail_attachments_mcp.auth import AuthError, LazySession
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
