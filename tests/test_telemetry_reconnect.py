"""Postgres backend must survive a dropped connection.

Regression test for a production 500: RDS closed the single long-lived
telemetry connection, and the next read (cost guard on POST /studio/chat)
raised ``psycopg.OperationalError: the connection is closed`` straight out
to the client.  The backend now reconnects-and-retries, and the cost guard
fails open.
"""
from __future__ import annotations

import sevim.telemetry as T
from studio import sessions


class _OpErr(Exception):
    pass


class _IfErr(Exception):
    pass


class _FakePsycopg:
    OperationalError = _OpErr
    InterfaceError = _IfErr

    def __init__(self):
        self.connect_calls = 0

    def connect(self, url, autocommit=False):
        self.connect_calls += 1
        return _FakeConn()


class _FakeCursor:
    def execute(self, q, p):
        pass

    def fetchall(self):
        return [(42,)]


class _FakeConn:
    def __init__(self, dead=False):
        self.closed = 0
        self._dead = dead
        self.rolled_back = False

    def cursor(self):
        if self._dead:
            raise _OpErr("the connection is closed")
        return _FakeCursor()

    def close(self):
        self.closed = 1

    def commit(self):
        pass

    def rollback(self):
        self.rolled_back = True


def _backend_with(conn, fake):
    """Build a _PostgresBackend without going through __init__ (no real DB)."""
    b = T._PostgresBackend.__new__(T._PostgresBackend)
    b._psycopg = fake
    b._db_url = "postgresql://x"
    b._conn = conn
    return b


def test_execute_reconnects_after_dead_connection():
    fake = _FakePsycopg()
    b = _backend_with(_FakeConn(dead=True), fake)
    cur = b.execute("SELECT 1", ())
    assert cur.fetchall() == [(42,)]
    assert fake.connect_calls == 1  # reconnected exactly once


def test_execute_reconnects_when_conn_already_closed():
    fake = _FakePsycopg()
    dead = _FakeConn()
    dead.closed = 1                      # proactively detected as closed
    b = _backend_with(dead, fake)
    cur = b.execute("SELECT 1", ())
    assert cur.fetchall() == [(42,)]
    assert fake.connect_calls == 1


def test_real_query_error_rolls_back_and_reraises():
    fake = _FakePsycopg()

    class _BadConn(_FakeConn):
        def cursor(self):
            raise ValueError("syntax error")  # not a connection error

    bad = _BadConn()
    b = _backend_with(bad, fake)
    try:
        b.execute("SELECT bad", ())
        assert False, "should have re-raised"
    except ValueError:
        pass
    assert bad.rolled_back is True        # aborted-tx state cleared
    assert fake.connect_calls == 0        # did NOT reconnect on a logic error


def test_commit_reconnects_on_dead_connection():
    fake = _FakePsycopg()

    class _CommitDead(_FakeConn):
        def commit(self):
            raise _IfErr("the connection is closed")

    b = _backend_with(_CommitDead(), fake)
    b.commit()                            # must not raise
    assert fake.connect_calls == 1


def test_cost_guard_fails_open_on_telemetry_error(monkeypatch):
    monkeypatch.setenv("SEVIM_COST_GUARD", "1")

    class _BoomTel:
        def session_cost(self, *a, **k):
            raise RuntimeError("the connection is closed")

    monkeypatch.setattr("sevim.telemetry.get_telemetry", lambda: _BoomTel())
    # Fails open: returns None (allow the request) instead of raising.
    assert sessions.check_cost_guard("sess-123") is None
