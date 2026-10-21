"""Saving attachments to a private directory without following planted symlinks or FIFOs.

Adapted from gmail-takeout-mcp so both servers save files the same way.
"""

from __future__ import annotations

import ctypes
import json
import mimetypes
import os
import re
import secrets
import stat
import sys
import time

# Saved paths end up in shell commands, and the sender picks the filename, so none of it goes into
# the path except an extension made only of these characters.
_EXTENSION_RE = re.compile(r"[a-z0-9]{1,10}")
# Lists each saved name's original filename, since the names on disk don't carry it.
INDEX_NAME = "filenames.json"

_QUARANTINE = b"com.apple.quarantine"
_AGENT = "gmail-attachments-mcp"
if sys.platform == "darwin":
    _libc = ctypes.CDLL(None, use_errno=True)
    _libc.fsetxattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t,
                                ctypes.c_uint32, ctypes.c_int]
    _libc.fgetxattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t,
                                ctypes.c_uint32, ctypes.c_int]
    _libc.fgetxattr.restype = ctypes.c_ssize_t


class QuarantineError(ValueError):
    pass


def attachment_name(stem: str, filename: str | None, mime_type: str) -> str:
    """`stem` plus the attachment's extension: the one in its filename if that is plain letters
    and digits, else one guessed from its MIME type, else none."""
    _, dot, ext = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].rpartition(".")
    ext = ext.lower() if dot else ""
    if not _EXTENSION_RE.fullmatch(ext):
        ext = (mimetypes.guess_extension(mime_type) or "").removeprefix(".")
    return f"{stem}.{ext}" if _EXTENSION_RE.fullmatch(ext) else stem


def index_json(filenames: dict[str, str | None]) -> bytes:
    """The contents of INDEX_NAME: each saved name mapped to its original filename."""
    return json.dumps(filenames, indent=2, ensure_ascii=False).encode() + b"\n"


def quarantine(fd: int) -> None:
    """Mark a saved file as downloaded, as Mail does, so macOS checks it (Gatekeeper) before it
    opens. Raises QuarantineError where the filesystem can't hold the mark, rather than leaving
    the file unprotected."""
    if sys.platform == "darwin":
        value = f"0081;{int(time.time()):08x};{_AGENT};".encode()
        if _libc.fsetxattr(fd, _QUARANTINE, value, len(value), 0, 0) != 0:
            raise QuarantineError(
                f"Couldn't quarantine the saved attachment ({os.strerror(ctypes.get_errno())}). "
                "Choose an --attachments-dir on a disk that supports extended attributes."
            )


def is_quarantined(fd: int) -> bool:
    return sys.platform != "darwin" or _libc.fgetxattr(fd, _QUARANTINE, None, 0, 0, 0) >= 0


def open_private_dir(path: str) -> int:
    """Create (if needed) and open a directory only this user can write to; return its fd.

    All later file operations go through the fd, so swapping the path for a symlink or another
    directory afterwards can't redirect them.
    """
    os.makedirs(path, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    st = os.fstat(fd)
    if st.st_uid != os.getuid():
        os.close(fd)
        raise ValueError(f"Refusing to save attachments in {path}: owned by another user")
    if stat.S_IMODE(st.st_mode) != 0o700:
        os.fchmod(fd, 0o700)
    return fd


def has_content(dir_fd: int, name: str, data: bytes) -> bool:
    """Whether `name` is already our private, quarantined regular file holding exactly `data`."""
    try:
        # O_NOFOLLOW rejects symlinks; O_NONBLOCK keeps a planted FIFO from hanging the open.
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except OSError:
        return False
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(fd)
        return (stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid()
                and stat.S_IMODE(st.st_mode) == 0o600 and st.st_size == len(data)
                and is_quarantined(fd) and f.read() == data)


def write_atomic(dir_fd: int, name: str, data: bytes, downloaded: bool = True) -> None:
    """Write `data` to a fresh temp file and rename it over `name`, replacing (never following)
    whatever is there. `downloaded` files are quarantined."""
    tmp = f".{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            if downloaded:
                quarantine(fd)
        os.rename(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except OSError:
            pass
        raise
