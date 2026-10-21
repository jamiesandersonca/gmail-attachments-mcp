"""Saving attachments to a private directory without following planted symlinks or FIFOs.

Adapted from gmail-takeout-mcp so both servers save files the same way.
"""

from __future__ import annotations

import ctypes
import mimetypes
import os
import re
import secrets
import stat
import sys
import time
import unicodedata

# Anything but letters, digits and a few punctuation marks. Saved paths end up in shell commands,
# so `$`, backticks, `;`, `&`, quotes and the like must not survive; nor must invisible formatting
# characters such as right-to-left overrides, which can disguise an extension.
_UNSAFE_FILENAME_RE = re.compile(r"[^\w .,+=@()\[\]-]+")

_QUARANTINE = b"com.apple.quarantine"
_AGENT = "gmail-attachments-mcp"
if sys.platform == "darwin":
    _libc = ctypes.CDLL(None, use_errno=True)
    _libc.fsetxattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t,
                                ctypes.c_uint32, ctypes.c_int]
    _libc.fgetxattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t,
                                ctypes.c_uint32, ctypes.c_int]
    _libc.fgetxattr.restype = ctypes.c_ssize_t


def safe_filename(name: str | None, fallback: str, mime_type: str) -> str:
    """A filesystem-safe basename for an attachment (never a path)."""
    name = unicodedata.normalize("NFC", name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _UNSAFE_FILENAME_RE.sub("_", name).strip().lstrip(".").strip()
    if not name or name == "noname":
        return fallback + (mimetypes.guess_extension(mime_type) or "")
    if len(name) > 200:
        stem, dot, ext = name.rpartition(".")
        name = stem[: 200 - len(ext) - 1] + dot + ext if dot and len(ext) <= 10 else name[:200]
    return name


def quarantine(fd: int) -> None:
    """Mark a saved file as downloaded, as Mail does, so macOS checks it (Gatekeeper) before it
    opens. Best effort: a filesystem without extended attributes just goes unmarked."""
    if sys.platform == "darwin":
        value = f"0081;{int(time.time()):08x};{_AGENT};".encode()
        _libc.fsetxattr(fd, _QUARANTINE, value, len(value), 0, 0)


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


def write_atomic(dir_fd: int, name: str, data: bytes) -> None:
    """Write `data` to a fresh temp file and rename it over `name`, replacing (never following)
    whatever is there."""
    tmp = f".{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            quarantine(fd)
        os.rename(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except OSError:
            pass
        raise
