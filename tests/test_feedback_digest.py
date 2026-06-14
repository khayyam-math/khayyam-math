"""Tests for the daily 'Not quite right?' feedback digest builder.

`build_digest` is a pure function over rows + a clock so it can be tested
without telemetry or SES.  Rows are
``(feedback_id, timestamp, original_prompt, user_description, canvas_id,
git_sha)`` for OPEN reports, newest first.
"""
from __future__ import annotations

from studio import feedback_digest as D

_NOW = 1_700_000_000.0  # fixed clock; no Date.now() in tests
_DAY = 86400.0


def _row(fid, age_s, prompt="explain X", desc="looks wrong",
         canvas="cv1", sha="abcdef123456"):
    return (fid, _NOW - age_s, prompt, desc, canvas, sha)


def test_empty_returns_none():
    assert D.build_digest([], _NOW) is None


def test_single_new_report():
    res = D.build_digest([_row(1, 3600)], _NOW)
    assert res is not None
    subject, body = res
    assert "1 new" in subject and "0 older" in subject
    assert "explain X" in body
    assert "looks wrong" in body
    assert "cv1" in body
    assert D._ADMIN_URL in body


def test_new_vs_older_split():
    rows = [_row(3, 3600), _row(2, _DAY * 2), _row(1, _DAY * 10)]
    subject, body = D.build_digest(rows, _NOW)
    # one within 24h, two older than ~1 day
    assert "1 new" in subject and "2 older" in subject
    assert "NEW in the last 24" in body
    assert "Still open from before" in body


def test_older_overflow_truncates_to_30():
    rows = [_row(i, _DAY * (i + 2)) for i in range(40)]
    subject, body = D.build_digest(rows, _NOW)
    assert "0 new" in subject and "40 older" in subject
    assert "and 10 more open reports" in body


def test_long_fields_are_clamped():
    rows = [_row(1, 3600, prompt="p" * 500, desc="d" * 800)]
    subject, body = D.build_digest(rows, _NOW)
    # prompt clamped to 140, desc to 320 — body must not echo the full blobs
    assert "p" * 200 not in body
    assert "d" * 400 not in body
