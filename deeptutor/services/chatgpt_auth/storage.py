"""Atomic, process-locked credentials; Windows additionally uses user-bound DPAPI."""

from __future__ import annotations

import base64
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import threading
from typing import Iterator
import uuid

from deeptutor.services.codex_auth.storage import (
    _assert_safe_directory,
    _assert_safe_regular_path,
    _atomic_write_json,
    _locked_file,
)


def _dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), buffer)
    result = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError("Could not protect the local ChatGPT credentials.")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


class ChatGPTStore:
    def __init__(self, secrets_root: Path) -> None:
        self.root = Path(secrets_root) / "private" / "openai-chatgpt"
        self.path = self.root / "accounts.v1.json"
        self._lock = threading.Lock()

    @contextmanager
    def locked(self) -> Iterator[dict]:
        with self._lock:
            for path in (self.root.parent, self.root):
                _assert_safe_directory(path)
                path.mkdir(parents=True, exist_ok=True)
                os.chmod(path, 0o700)
            with _locked_file(self.root / "accounts.lock"):
                _assert_safe_regular_path(self.path)
                if self.path.exists():
                    stored = json.loads(self.path.read_text(encoding="utf-8"))
                    if "dpapi" in stored:
                        stored = json.loads(_dpapi(base64.b64decode(stored["dpapi"]), decrypt=True))
                    if stored.get("schema_version") != 1 or not isinstance(
                        stored.get("accounts"), dict
                    ):
                        raise ValueError("Stored ChatGPT credentials are invalid.")
                else:
                    stored = {
                        "schema_version": 1,
                        "host_id": f"urn:uuid:{uuid.uuid4()}",
                        "active_id": None,
                        "accounts": {},
                    }
                yield stored
                self.save(stored)

    def save(self, stored: dict) -> None:
        # Call only while holding locked(); refresh replaces the complete token set.
        if os.name == "nt":
            encrypted = _dpapi(json.dumps(stored).encode("utf-8"))
            payload = {"dpapi": base64.b64encode(encrypted).decode("ascii")}
        else:
            payload = stored
        _atomic_write_json(self.path, payload)
