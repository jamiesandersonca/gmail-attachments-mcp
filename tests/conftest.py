"""A fake Gmail API session serving a few messages, so tests never touch the network."""

from __future__ import annotations

import base64
import itertools

import pytest

from gmail_attachments_mcp.gmail import API, Downloader

PDF = b"%PDF-1.4 fake invoice"
CSV = b"date,amount\n2026-01-01,12.50\n"
CSV2 = b"date,amount\n2026-02-01,99.99\n"
PNG = b"\x89PNG fake image"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def leaf(part_id, filename, mime, data, inline=False):
    body = {"size": len(data)}
    if inline:
        body["data"] = b64(data)
    else:
        body["attachmentId"] = f"ANGj{part_id}"  # rewritten per fetch by FakeSession
    return {"partId": part_id, "filename": filename, "mimeType": mime, "body": body, "_data": data}


MESSAGES = {
    # One PDF, like the connector's typical invoice mail.
    "1a0000000000000a": {"mimeType": "multipart/mixed", "partId": "", "parts": [
        {"partId": "0", "mimeType": "text/plain", "filename": "", "body": {"size": 5, "data": b64(b"hello")}},
        leaf("1", "invoice.pdf", "application/pdf", PDF),
    ]},
    # Two CSVs with the same name (case differs) and size, plus an inline image and a nameless part.
    "1a0000000000000b": {"mimeType": "multipart/mixed", "partId": "", "parts": [
        {"partId": "0", "mimeType": "multipart/alternative", "filename": "", "body": {"size": 0}, "parts": [
            {"partId": "0.0", "mimeType": "text/plain", "filename": "", "body": {"size": 2, "data": b64(b"hi")}},
        ]},
        leaf("1", "report.csv", "text/csv", CSV),
        leaf("2", "REPORT.csv", "text/csv", CSV2),
        leaf("3", "logo.png", "image/png", PNG, inline=True),
        leaf("4", "", "application/pdf", PDF),
    ]},
    "1a0000000000000c": {"mimeType": "text/plain", "partId": "", "filename": "",
                         "body": {"size": 2, "data": b64(b"no")}},
    # An empty attachment: Gmail sends neither data nor an attachment ID.
    "1a0000000000000d": {"mimeType": "multipart/mixed", "partId": "", "parts": [
        {"partId": "0", "mimeType": "text/plain", "filename": "", "body": {"size": 2, "data": b64(b"hi")}},
        {"partId": "1", "mimeType": "text/plain", "filename": "empty.txt", "body": {"size": 0}},
        leaf("2", "invoice.pdf", "application/pdf", PDF),
    ]},
}


class Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeSession:
    """Mimics Gmail's habit of issuing new attachment IDs on every messages.get."""

    def __init__(self):
        self.calls = []
        self._fetch = itertools.count(1)
        self._issued: dict[str, bytes] = {}
        self.fail = None  # a Response to return, or an exception to raise, for every call

    def issue_id(self, message_id, part_id) -> str:
        """An attachment ID as an earlier connector call would have seen it."""
        part = _find(MESSAGES[message_id], part_id)
        aid = f"OLD{next(self._fetch)}x{part_id}"
        self._issued[aid] = part["_data"]
        return aid

    def issue_unlisted_id(self, data: bytes) -> str:
        """An attachment ID for data that isn't one of the parts the message lists."""
        aid = f"OLD{next(self._fetch)}xunlisted"
        self._issued[aid] = data
        return aid

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        if isinstance(self.fail, Exception):
            raise self.fail
        if self.fail:
            return self.fail
        path = url.removeprefix(API + "/").split("/")
        if path == ["profile"]:
            return Response(200, {"emailAddress": "me@example.com"})
        if path[0] != "messages" or path[1] not in MESSAGES:
            return Response(404)
        message = MESSAGES[path[1]]
        if len(path) == 2:
            n = next(self._fetch)
            return Response(200, {"id": path[1], "payload": self._render(message, n)})
        data = self._issued.get(path[3])
        return Response(200, {"size": len(data), "data": b64(data)}) if data else Response(400)

    def _render(self, part, n):
        out = {k: v for k, v in part.items() if k not in ("_data", "parts", "body")}
        out["body"] = dict(part.get("body", {}))
        if "attachmentId" in out["body"]:
            out["body"]["attachmentId"] = aid = f"NEW{n}x{part['partId']}"
            self._issued[aid] = part["_data"]
        if "parts" in part:
            out["parts"] = [self._render(p, n) for p in part["parts"]]
        return out


def _find(part, part_id):
    if part.get("partId") == part_id and "_data" in part:
        return part
    for p in part.get("parts", []):
        if found := _find(p, part_id):
            return found
    return None


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def downloader(session, tmp_path):
    return Downloader(session, str(tmp_path / "attachments"))
