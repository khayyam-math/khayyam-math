"""Pluggable object storage for canvas-side artefacts (narration WAVs).

The viewer serves audio at ``/canvas/<id>/narration.wav`` and
``/canvas/<id>/intro.wav``.  On a single laptop those land on local
disk under ``SEVIM_DATA_DIR`` and that's the end of it.  On AWS Fargate
local disk is ephemeral — when ECS replaces a task, every file in
``/var/sevim/canvases/`` disappears.  This module is the durability
layer: every WAV gets uploaded to S3 best-effort right after synthesis,
and the viewer endpoint falls back to a presigned S3 URL when the local
copy isn't there.

Backend selection is controlled by ``SEVIM_STORAGE_URL``:

  * unset, or ``file:///path``  → :class:`FileStorage` (no-op uploads;
                                  local disk is the system of record).
  * ``s3://bucket`` or
    ``s3://bucket/prefix``       → :class:`S3Storage` (boto3, presigned
                                  URLs valid for one hour by default).

Both backends are best-effort on write: an S3 upload failure logs to
stderr but doesn't raise, because the local disk copy is still good
enough for the user who just generated the figure.  Durability across
task replacement is the bonus we aim for, not a hard contract.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class Storage(Protocol):
    def upload_file(self, local_path: Path, key: str,
                    content_type: str = "application/octet-stream") -> None: ...

    def presigned_get_url(self, key: str, expires_s: int = 3600) -> str | None: ...

    def is_remote(self) -> bool: ...


# ---------------------------------------------------------------------------
# FileStorage — local disk, the dev default.  upload is a no-op because
# the canonical copy is already on disk; presigned URLs are not applicable.
# ---------------------------------------------------------------------------

class FileStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def upload_file(self, local_path: Path, key: str,
                    content_type: str = "application/octet-stream") -> None:
        # File already lives at its canonical local path; nothing to do.
        # Kept as a method so call sites are backend-agnostic.
        return

    def presigned_get_url(self, key: str, expires_s: int = 3600) -> str | None:
        # No remote URL for a local-disk backend.
        return None

    def is_remote(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# S3Storage — durable backend for AWS deploys.  Reads come back as a
# presigned URL so the browser fetches directly from S3 (skipping the
# Fargate task as a relay) — keeps task CPU/network free for new turns.
# ---------------------------------------------------------------------------

class S3Storage:
    def __init__(self, bucket: str, prefix: str = "") -> None:
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "S3 storage selected (SEVIM_STORAGE_URL=s3://...) but boto3 "
                "is not installed.  Install with: pip install 'sevim[aws]'"
            ) from exc
        self._s3 = boto3.client("s3")
        self.bucket = bucket
        self.prefix = (prefix or "").strip("/")

    def _qualified_key(self, key: str) -> str:
        # Avoid leading slashes; S3 will accept them but they create
        # surprising "" parent prefixes in the console.
        if self.prefix:
            return f"{self.prefix}/{key.lstrip('/')}"
        return key.lstrip("/")

    def upload_file(self, local_path: Path, key: str,
                    content_type: str = "application/octet-stream") -> None:
        try:
            self._s3.upload_file(
                Filename=str(local_path),
                Bucket=self.bucket,
                Key=self._qualified_key(key),
                ExtraArgs={"ContentType": content_type},
            )
        except Exception as exc:  # noqa: BLE001 — silent best-effort
            print(f"[storage] S3 upload failed for {key!r}: "
                  f"{type(exc).__name__}: {exc}",
                  flush=True, file=sys.stderr)

    def presigned_get_url(self, key: str, expires_s: int = 3600) -> str | None:
        try:
            return self._s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": self._qualified_key(key)},
                ExpiresIn=expires_s,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[storage] presigned URL failed for {key!r}: "
                  f"{type(exc).__name__}: {exc}",
                  flush=True, file=sys.stderr)
            return None

    def is_remote(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Module-level factory + singleton.
# ---------------------------------------------------------------------------

_INSTANCE: Storage | None = None


def get_storage() -> Storage:
    """Return the configured storage backend, creating it on first call.

    The backend is sticky for the lifetime of the process; re-evaluate
    SEVIM_STORAGE_URL with :func:`reset_for_tests` if you need a swap.
    """
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE
    url = os.environ.get("SEVIM_STORAGE_URL", "").strip()
    if url.startswith("s3://"):
        parsed = urlparse(url)
        bucket = parsed.netloc
        if not bucket:
            raise RuntimeError(f"SEVIM_STORAGE_URL={url!r} has no bucket")
        prefix = parsed.path.lstrip("/")
        _INSTANCE = S3Storage(bucket=bucket, prefix=prefix)
        return _INSTANCE
    # FileStorage — derive root from explicit URL, env var, or default.
    if url.startswith("file://"):
        root = Path(urlparse(url).path)
    else:
        root = Path(
            os.environ.get("SEVIM_DATA_DIR")
            or (Path.home() / ".local" / "share" / "sevim" / "canvases")
        )
    _INSTANCE = FileStorage(root=root)
    return _INSTANCE


def reset_for_tests() -> None:
    """Clear the cached singleton — call between tests that change env."""
    global _INSTANCE
    _INSTANCE = None
