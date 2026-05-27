"""Server-side IP → geo lookup using MaxMind GeoLite2.

Free monthly download from MaxMind; no network call at request time.
Set ``SEVIM_GEOLITE_DB`` to the path of the ``.mmdb`` file. If the env
var is unset or the file/library is missing, ``lookup()`` returns all-None
so callers can treat the user's location as "unknown" without branching.

The ``geoip2.Reader`` is thread-safe per its docs, so we keep a single
process-wide instance behind a lock-free lazy init.
"""
from __future__ import annotations

import os
import sys
from typing import NamedTuple


class GeoResult(NamedTuple):
    country: str | None  # ISO-3166 alpha-2, e.g. "DE"
    region: str | None   # subdivision name, e.g. "Bavaria"
    city: str | None     # e.g. "Munich"


_EMPTY = GeoResult(None, None, None)
_reader: object | None = None
_reader_init_done = False


def _init_reader() -> object | None:
    global _reader, _reader_init_done
    if _reader_init_done:
        return _reader
    _reader_init_done = True

    db_path = os.environ.get("SEVIM_GEOLITE_DB", "").strip()
    if not db_path or not os.path.isfile(db_path):
        return None
    try:
        import geoip2.database  # type: ignore
    except ImportError:
        print("[geoip] geoip2 not installed; skipping IP lookups",
              flush=True, file=sys.stderr)
        return None
    try:
        _reader = geoip2.database.Reader(db_path)
        print(f"[geoip] loaded GeoLite2 DB from {db_path}",
              flush=True, file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[geoip] failed to open {db_path}: {exc}",
              flush=True, file=sys.stderr)
        _reader = None
    return _reader


def lookup(ip: str | None) -> GeoResult:
    """Resolve an IP to (country, region, city). Always fail-safe."""
    if not ip:
        return _EMPTY
    reader = _init_reader()
    if reader is None:
        return _EMPTY
    try:
        resp = reader.city(ip)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — bad IP, private range, not-in-db, etc.
        return _EMPTY
    return GeoResult(
        country=getattr(resp.country, "iso_code", None),
        region=(resp.subdivisions.most_specific.name
                if resp.subdivisions else None),
        city=getattr(resp.city, "name", None),
    )
