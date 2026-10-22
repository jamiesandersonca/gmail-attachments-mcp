import pytest

from gmail_attachments_mcp import cli

from .conftest import Response


@pytest.fixture
def auth_cli(monkeypatch, session, tmp_path):
    monkeypatch.setattr(cli, "authorize", lambda client_secret, token: None)
    monkeypatch.setattr(cli, "load_session", lambda token: session)
    return lambda: cli.main(["--token", str(tmp_path / "token.json"), "auth"])


def test_auth_reports_the_account(auth_cli, capsys):
    auth_cli()
    assert "Authorized me@example.com." in capsys.readouterr().out


def test_auth_fails_when_gmail_refuses_the_check(auth_cli, session, capsys):
    session.fail = Response(403, {"error": {"message": "Gmail API has not been used in project 1",
                                            "errors": [{"reason": "accessNotConfigured"}]}})
    with pytest.raises(SystemExit, match="isn't enabled .*project 1"):
        auth_cli()
    assert "Authorized" not in capsys.readouterr().out
