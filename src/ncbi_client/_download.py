from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

import httpx

# Downloads can transfer multi-GB files; give them a much longer read timeout
# than the client's default 30s, without changing that default for every
# other (small, JSON) request the client makes.
DOWNLOAD_TIMEOUT = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)


def stream_to_path(destination: Path, open_response: Callable[[], AbstractContextManager[httpx.Response]]) -> None:
    """Stream an HTTP response body to `destination` atomically.

    `open_response` is a zero-arg callable returning a context manager that
    yields an httpx.Response when entered (e.g. `lambda: client.http.stream(...)`).
    Writes to a sibling temp file and renames into place on success, so a
    failed or interrupted download never leaves a corrupt file at `destination`.
    """
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(destination.parent), prefix=f".{destination.name}.", suffix=".part")
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            with open_response() as resp:
                resp.raise_for_status()
                for chunk in resp.iter_bytes():
                    f.write(chunk)
        os.replace(tmp_path, destination)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise
