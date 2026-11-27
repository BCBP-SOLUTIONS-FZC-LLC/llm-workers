"""AES-GCM payload codec (LLD WS-13): nothing readable is ever written to Temporal history.

Every payload is encrypted with a key looked up by id; the key id is in the payload metadata, so
a rotated key still decrypts old history. The workflow input holds identifiers only (ASYNC-12), so
this is defence in depth: an export of history is unreadable without the key, and a codec server
(behind single sign-on) decodes it for an operator without the key leaving the platform.

The key comes from a ``KeyProvider`` port. This repository imports no cloud SDK (WD-3), so the
KMS-unwrapped data key is mounted by the platform secret store (ADR-0002, PL-2 open).

Replay safety: the codec runs outside the workflow sandbox, on the data converter.
Security: random 96-bit nonce per payload; the key id is authenticated as associated data.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec

ENCODING = b"binary/encrypted"
KEY_ID_METADATA = "encryption-key-id"
NONCE_BYTES = 12


class KeyProvider(Protocol):
    """Where keys come from."""

    def current_key_id(self) -> str:
        """The id new payloads are encrypted with."""

    def key_for(self, key_id: str) -> bytes:
        """The 32-byte key for ``key_id``. Raises ``KeyError`` for an unknown id."""


class StaticKeyProvider:
    """Keys by id from memory, for tests and for a platform that mounts unwrapped keys."""

    def __init__(self, keys: dict[str, bytes], current: str) -> None:
        if current not in keys:
            raise KeyError(current)
        if any(len(k) != 32 for k in keys.values()):
            raise ValueError("AES-256 keys are 32 bytes")
        self._keys, self._current = dict(keys), current

    @classmethod
    def from_file(cls, key_id: str, path: str | Path) -> StaticKeyProvider:
        """One key id and a mounted file holding its 32 raw bytes."""
        return cls({key_id: Path(path).read_bytes()}, key_id)

    def current_key_id(self) -> str:
        """The key new payloads use."""
        return self._current

    def key_for(self, key_id: str) -> bytes:
        """The key of ``key_id``."""
        return self._keys[key_id]


class AesGcmPayloadCodec(PayloadCodec):
    """Encrypt on the way into history, decrypt on the way out."""

    def __init__(self, keys: KeyProvider) -> None:
        self._keys = keys

    async def encode(self, payloads: Iterable[Payload]) -> list[Payload]:
        """Encrypt each payload's whole serialised form."""
        key_id = self._keys.current_key_id()
        aead = AESGCM(self._keys.key_for(key_id))
        out: list[Payload] = []
        for p in payloads:
            nonce = os.urandom(NONCE_BYTES)
            sealed = aead.encrypt(nonce, p.SerializeToString(), key_id.encode())
            out.append(
                Payload(
                    metadata={"encoding": ENCODING, KEY_ID_METADATA: key_id.encode()},
                    data=nonce + sealed,
                )
            )
        return out

    async def decode(self, payloads: Iterable[Payload]) -> list[Payload]:
        """Decrypt payloads this codec wrote; pass any other through unchanged."""
        out: list[Payload] = []
        for p in payloads:
            if p.metadata.get("encoding") != ENCODING:
                out.append(p)
                continue
            key_id = p.metadata[KEY_ID_METADATA]
            aead = AESGCM(self._keys.key_for(key_id.decode()))
            plain = aead.decrypt(p.data[:NONCE_BYTES], p.data[NONCE_BYTES:], key_id)
            decoded = Payload()
            decoded.ParseFromString(plain)
            out.append(decoded)
        return out
