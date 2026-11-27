"""Throwaway infrastructure for the real-engine test: Postgres 18, an S3 emulator and a Tender stub.

Isolated per run: each container is started here with a random host port and removed afterwards, so
no shared or developer server is ever touched. ``docker`` is the only requirement (no extra library).
Postgres 18 is needed for ``uuidv7()``; floci is the S3-compatible emulator the engine's own dev stack
uses; Valkey is the pack cache the engine's container wires.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

LABEL = "llm-workers-real-engine-test"


def docker(*args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed docker CLI, test-only
        ["docker", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout.strip()


@contextmanager
def container(image: str, port: int, *env: str) -> Iterator[tuple[str, int]]:
    """Run ``image`` detached on a random localhost port; yield ``(host, mapped_port)``."""
    args = ["run", "-d", "--rm", "--label", LABEL, "-p", f"127.0.0.1::{port}"]
    for item in env:
        args += ["-e", item]
    cid = docker(*args, image)
    try:
        mapped = docker("port", cid, f"{port}/tcp").splitlines()[0].rsplit(":", 1)[1]
        yield "127.0.0.1", int(mapped)
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True, check=False)  # noqa: S603, S607


async def wait_for(check: Any, what: str, seconds: float = 60) -> None:
    """Retry an async ``check`` until it stops raising."""
    deadline = time.monotonic() + seconds
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            await check()
            return
        except Exception as exc:  # noqa: BLE001 - any failure means "not ready yet"
            last = exc
            await asyncio.sleep(0.5)
    raise TimeoutError(f"{what} not ready after {seconds}s: {last!r}")


class TenderStub:
    """A Tender pack endpoint (``GET /internal/packs/resolve``) serving one pack document.

    The engine's pack source is Tender over HTTP, so this is the one stand-in in the pack path: the
    resolver, the cache and the codec that read it are the engine's own.
    """

    def __init__(self, pack: dict[str, Any]) -> None:
        self.body = json.dumps(pack).encode()
        self.status = 200
        self.requests: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                owner.requests.append(self.path)
                self.send_response(owner.status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(owner.body)))
                self.end_headers()
                self.wfile.write(owner.body)

            def log_message(self, *args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def serve(self, pack: dict[str, Any]) -> None:
        """Change the pack the stub serves (the engine's cache is keyed by tenant: use a new one)."""
        self.body = json.dumps(pack).encode()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        self._server.shutdown()


def pack_document(capability: str) -> dict[str, Any]:
    """A minimal Domain Profile carrying the two assumed pack keys (``outline_skeletons``, ``fact_keys``)."""
    section = {
        "candidate_depth": 24,
        "rerank_in": 16,
        "rerank_out": 8,
        "sufficiency": {"min_top_score": 0.2, "min_chunks": 1, "chunk_score_floor": 0.1},
        "diversity_bound": 3,
    }
    return {
        "domain_profile": {
            "ref": "technical_proposal_profile",
            "version": "dp-1",
            "taxonomy_version": "tx-1",
            "default_profile": section,
            "outline_skeletons": {
                capability: [
                    {"ref": "1", "section_type": "executive_summary", "is_foundation": True},
                    {"ref": "2", "section_type": "methodology"},
                    {"ref": "3", "section_type": "pricing"},
                ]
            },
            "fact_keys": ["contract_value", "client_name"],
        },
        "language_pack": {"language": "en", "version": "lp-1"},
    }


def migrations_dir(tag_dir: str) -> Path:
    return Path(tag_dir) / "migrations"
