"""Fetch attachments from the Gmail API and save them under a private directory."""

from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass
from urllib.parse import quote

from google.auth.exceptions import RefreshError

from .auth import AuthError
from .files import (
    INDEX_NAME,
    attachment_name,
    has_content,
    index_json,
    open_private_dir,
    write_atomic,
)

API = "https://gmail.googleapis.com/gmail/v1/users/me"
DEFAULT_ATTACHMENTS_DIR = "~/.cache/gmail-attachments-mcp/attachments"
TIMEOUT = 60

_HEX_ID_RE = re.compile(r"[0-9a-f]{1,32}")
_ATTACHMENT_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
_PART_ID_RE = re.compile(r"[\d.]*")


class NotFound(ValueError):
    pass


class GmailError(ValueError):
    pass


@dataclass
class Part:
    part_id: str
    filename: str
    mime_type: str
    size: int
    attachment_id: str | None  # Gmail API ID; None when the data is inline in the message
    data: str | None


def normalize_message_id(value: str) -> str:
    """Accept the hex ID the connector uses, or the decimal form from Takeout / mail URLs."""
    v = value.strip().lower()
    if v.isdigit() and len(v) >= 18:  # hex IDs are 16 characters; decimal ones are 19
        v = format(int(v), "x")
    if not _HEX_ID_RE.fullmatch(v):
        raise NotFound(f"Invalid message ID: {value!r}")
    return v


class Downloader:
    def __init__(self, session, attachments_dir: str | None = None):
        self.session = session
        self.attachments_dir = os.path.expanduser(attachments_dir or DEFAULT_ATTACHMENTS_DIR)

    def _get(self, path: str, what: str, **params) -> dict:
        try:
            r = self.session.get(f"{API}/{path}", params=params, timeout=TIMEOUT)
        except RefreshError as e:
            raise AuthError(
                f"Gmail token could not be refreshed ({e}). Run `gmail-attachments-mcp auth` again."
            ) from e
        if r.status_code in (400, 404):
            raise NotFound(f"{what} not found")
        if r.status_code in (401, 403):
            raise AuthError(f"Gmail refused access ({r.status_code}). Run "
                            "`gmail-attachments-mcp auth` with the account the connector uses.")
        if r.status_code >= 300:
            raise GmailError(f"Gmail API error {r.status_code} fetching {what}")
        return r.json()

    def list_parts(self, message_id: str) -> list[Part]:
        msg = self._get(f"messages/{message_id}", f"Message {message_id}",
                        format="full", fields="id,payload")
        parts: list[Part] = []
        stack = [msg.get("payload") or {}]
        while stack:
            p = stack.pop()
            if p.get("parts"):
                stack.extend(reversed(p["parts"]))
                continue
            body = p.get("body") or {}
            if not p.get("filename") and not body.get("attachmentId"):
                continue  # a body part, not an attachment
            parts.append(Part(
                part_id=p.get("partId", ""),
                filename=p.get("filename") or "",
                mime_type=p.get("mimeType") or "application/octet-stream",
                size=body.get("size", 0),
                attachment_id=body.get("attachmentId"),
                data=body.get("data"),
            ))
        return parts

    def _fetch(self, message_id: str, attachment_id: str) -> bytes:
        att = self._get(f"messages/{message_id}/attachments/{quote(attachment_id, safe='')}",
                        f"Attachment in message {message_id}")
        return _b64(att.get("data", ""))

    def _data(self, message_id: str, part: Part) -> bytes:
        if part.data is not None:
            return _b64(part.data)
        return self._fetch(message_id, part.attachment_id)

    def download(self, message_id: str, attachment_id: str | None = None) -> dict:
        """Save one (or every) attachment of a message under attachments_dir; return the paths."""
        message_id = normalize_message_id(message_id)
        all_parts = self.list_parts(message_id)
        if not all_parts:
            raise NotFound(f"Message {message_id} has no attachments")

        prefetched: dict[str, bytes] = {}
        if attachment_id:
            attachment_id = attachment_id.strip()
            parts = [p for p in all_parts if attachment_id in (p.attachment_id, p.part_id)]
            if not parts:
                parts = self._match_stale_id(message_id, attachment_id, all_parts, prefetched)
        else:
            parts = all_parts

        names = _attachment_names(all_parts)
        msg_dir = os.path.join(os.path.realpath(self.attachments_dir), message_id)
        dir_fd = open_private_dir(msg_dir)
        saved = []
        try:
            index = index_json({names[p.part_id]: p.filename or None for p in all_parts})
            if not has_content(dir_fd, INDEX_NAME, index):
                write_atomic(dir_fd, INDEX_NAME, index)
            for part in parts:
                data = prefetched.get(part.part_id)
                if data is None:
                    data = self._data(message_id, part)
                name = names[part.part_id]
                if not has_content(dir_fd, name, data):
                    write_atomic(dir_fd, name, data)
                saved.append({
                    "partId": part.part_id,
                    "filename": part.filename or name,
                    "mimeType": part.mime_type,
                    "size": len(data),
                    "path": os.path.join(msg_dir, name),
                })
        finally:
            os.close(dir_fd)
        return {"messageId": message_id, "attachments": saved}

    def _match_stale_id(self, message_id: str, attachment_id: str, all_parts: list[Part],
                        prefetched: dict[str, bytes]) -> list[Part]:
        """Gmail issues a new attachment ID on every fetch of a message, so an ID the connector
        returned earlier rarely matches ours. It still downloads, though; identify which part it
        is by size."""
        valid = ", ".join(f"{p.part_id} ({p.filename or 'unnamed'})" for p in all_parts)
        not_found = NotFound(f"Attachment {attachment_id[:40]} not found in message {message_id} "
                             f"(parts with attachments: {valid}). Pass a partId instead.")
        if not _ATTACHMENT_ID_RE.fullmatch(attachment_id):
            raise not_found
        try:
            data = self._fetch(message_id, attachment_id)
        except NotFound:
            raise not_found from None
        candidates = [p for p in all_parts if p.size == len(data)] or all_parts
        if len(candidates) > 1:
            # Several parts of that size: compare contents to find which one this is.
            candidates = [p for p in candidates if self._data(message_id, p) == data][:1]
        if not candidates:
            raise not_found
        prefetched[candidates[0].part_id] = data
        return candidates


def _b64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _attachment_names(parts: list[Part]) -> dict[str, str]:
    """Map each partId to its on-disk name, `part<partId>.<ext>`, so none of the sender's filename
    goes into the path. Only leaf parts are listed, so no partId extends another (`1` and `1.2`)
    and the names are distinct."""
    for p in parts:
        if not _PART_ID_RE.fullmatch(p.part_id):
            raise GmailError(f"Unexpected partId from Gmail: {p.part_id[:40]!r}")
    names = {p.part_id: attachment_name(f"part{p.part_id}", p.filename, p.mime_type) for p in parts}
    if len(set(names.values())) != len(names):
        raise GmailError("Gmail returned attachment parts whose saved names would clash")
    return names
