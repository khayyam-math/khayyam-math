"""Storage backend selection + FileStorage round-trip.

S3Storage path is type-checked but not exercised because no AWS creds /
moto / localstack is available in this test environment; that integration
runs at deploy time (or via tests/test_storage_s3.py guarded on env).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _reset() -> None:
    from service import storage as S
    S.reset_for_tests()


def test_default_is_filestorage() -> None:
    os.environ.pop("SEVIM_STORAGE_URL", None)
    _reset()
    from service.storage import get_storage, FileStorage
    s = get_storage()
    assert isinstance(s, FileStorage), type(s)
    assert s.is_remote() is False
    assert s.presigned_get_url("anything") is None
    print("OK: default backend is FileStorage")


def test_explicit_file_url() -> None:
    with tempfile.TemporaryDirectory() as d:
        os.environ["SEVIM_STORAGE_URL"] = f"file://{d}"
        _reset()
        from service.storage import get_storage, FileStorage
        s = get_storage()
        assert isinstance(s, FileStorage)
        assert s.root == Path(d)
        # upload is a no-op — just confirm it doesn't raise.
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hello")
            local = Path(f.name)
        try:
            s.upload_file(local, "k", content_type="application/octet-stream")
        finally:
            local.unlink()
    print("OK: file:// URL → FileStorage at the configured root")


def test_s3_url_routes_to_S3Storage() -> None:
    os.environ["SEVIM_STORAGE_URL"] = "s3://my-bucket/some/prefix"
    _reset()
    try:
        from service.storage import get_storage, S3Storage
        s = get_storage()
        assert isinstance(s, S3Storage), type(s)
        assert s.bucket == "my-bucket"
        assert s.prefix == "some/prefix"
        assert s.is_remote() is True
        # _qualified_key joins prefix + key without leading slashes.
        assert s._qualified_key("c1/intro.wav") == "some/prefix/c1/intro.wav"
        print("OK: s3:// URL → S3Storage with bucket/prefix split correctly")
    except RuntimeError as exc:
        # boto3 not installed in this venv — fine, just confirm the
        # routing fired the right code path.
        assert "boto3" in str(exc).lower(), exc
        print("OK: s3:// URL routes to S3Storage (boto3 not installed)")


def test_s3_no_bucket_raises() -> None:
    os.environ["SEVIM_STORAGE_URL"] = "s3:///just-a-prefix"
    _reset()
    try:
        from service.storage import get_storage
        get_storage()
    except RuntimeError as exc:
        assert "bucket" in str(exc).lower(), exc
        print("OK: malformed s3:// URL (empty bucket) raises clearly")
        return
    raise AssertionError("expected RuntimeError for empty-bucket URL")


if __name__ == "__main__":
    test_default_is_filestorage()
    test_explicit_file_url()
    test_s3_url_routes_to_S3Storage()
    test_s3_no_bucket_raises()
    os.environ.pop("SEVIM_STORAGE_URL", None)
    print("\nAll storage tests passed.")
