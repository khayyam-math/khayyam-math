"""Thin wrapper around `claude -p --output-format json`.

Provides:
  * `call(model, system, user) -> dict`  — one JSON-mode Claude call with a
    minimal tool surface and an explicit system prompt; returns the parsed
    JSON envelope (incl. `total_cost_usd`).
  * `LedgerError` / `HARD_CAP_USD` — hard spend cap; the harness aborts
    rather than overspend.
  * `Ledger` — incremental persisted cost tracker.

The wrapper is the only path the eval harness uses to talk to Claude;
all token counts and costs flow through here.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

HARD_CAP_USD = 20.0
DEFAULT_TIMEOUT_S = 60

LEDGER_PATH = Path(__file__).parent / "results" / "cost_ledger.json"


class LedgerError(RuntimeError):
    """Raised when the cumulative spend would exceed the hard cap."""


@dataclass
class Ledger:
    spent_usd: float = 0.0
    calls: int = 0
    by_model: dict[str, dict] = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls) -> "Ledger":
        if LEDGER_PATH.exists():
            d = json.loads(LEDGER_PATH.read_text())
            return cls(**d)
        return cls()

    def save(self) -> None:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        LEDGER_PATH.write_text(json.dumps({
            "spent_usd": round(self.spent_usd, 6),
            "calls": self.calls,
            "by_model": self.by_model,
            "log": self.log[-200:],   # cap log size
        }, indent=2))

    def record(self, model: str, cost_usd: float, in_tok: int,
               out_tok: int, label: str) -> None:
        self.calls += 1
        self.spent_usd += cost_usd
        bm = self.by_model.setdefault(model, {
            "calls": 0, "spent_usd": 0.0,
            "input_tokens": 0, "output_tokens": 0,
        })
        bm["calls"] += 1
        bm["spent_usd"] = round(bm["spent_usd"] + cost_usd, 6)
        bm["input_tokens"] += in_tok
        bm["output_tokens"] += out_tok
        self.log.append({
            "ts": time.time(), "label": label, "model": model,
            "cost_usd": cost_usd, "input_tokens": in_tok,
            "output_tokens": out_tok,
        })


def call(model: str, system: str, user: str,
         label: str = "", timeout_s: int = DEFAULT_TIMEOUT_S,
         ledger: Ledger | None = None) -> dict:
    """One Claude call. Hard-caps cumulative spend at HARD_CAP_USD."""
    own_ledger = ledger is None
    if ledger is None:
        ledger = Ledger.load()
    if ledger.spent_usd >= HARD_CAP_USD:
        raise LedgerError(
            f"hard cap ${HARD_CAP_USD} hit (spent ${ledger.spent_usd:.4f})")

    cmd = [
        "claude", "-p",
        "--model", model,
        "--tools", "",
        "--system-prompt", system,
        "--output-format", "json",
        user,
    ]
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout_s)
    dt_s = time.perf_counter() - t0
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude -p failed (exit {proc.returncode}): {proc.stderr[:400]}")
    env = json.loads(proc.stdout)
    cost = float(env.get("total_cost_usd", 0.0))
    in_tok = int(env.get("usage", {}).get("input_tokens", 0))
    out_tok = int(env.get("usage", {}).get("output_tokens", 0))
    ledger.record(model, cost, in_tok, out_tok, label)
    if own_ledger:
        ledger.save()
    env["_wall_clock_s"] = round(dt_s, 4)
    return env
