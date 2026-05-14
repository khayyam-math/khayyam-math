"""FastAPI inference server for the neural-layout corrector.

Phase D deployment target: CPU Fargate, 0.5 vCPU / 1 GB. Spin-up
cold start should be < 2 s (model is ~10 MB).

Endpoints:
    POST /layout/correct
        body: {"svg": "<svg…>", "viewport_kind": "desktop|phone"}
        returns: {"source_graph": …, "predicted_graph": …,
                  "deltas": [...], "warnings": [...]}

    GET /healthz
        returns: {"status": "ok", "checkpoint": "<path>", "device": "cpu",
                  "n_params": ...}

Run locally:
    SEVIM_NEURAL_LAYOUT_CKPT=runs/gnn_v1/best.pt \\
    .venv/bin/uvicorn studio.neural_layout.server:app --port 8765
"""
from __future__ import annotations

import os
from pathlib import Path

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .inference import correct_layout_svg, load_model

_CKPT_PATH = os.environ.get(
    "SEVIM_NEURAL_LAYOUT_CKPT",
    "runs/gnn_v1/best.pt",
)
_DEVICE = os.environ.get("SEVIM_NEURAL_LAYOUT_DEVICE", "cpu")


class CorrectRequest(BaseModel):
    svg: str
    viewport_kind: str = "desktop"


class CorrectResponse(BaseModel):
    source: dict
    predicted: dict
    n_nodes: int
    n_protected_pinned: int
    warnings: list[str]


app = FastAPI(
    title="Khayyam Math — Neural Layout Corrector",
    version="0.1.0",
)
_model: torch.nn.Module | None = None
_n_params = 0


def _ensure_model() -> torch.nn.Module:
    global _model, _n_params
    if _model is None:
        path = Path(_CKPT_PATH)
        if not path.exists():
            raise HTTPException(
                503,
                f"Checkpoint not loaded yet: {path} not found. "
                f"Set SEVIM_NEURAL_LAYOUT_CKPT.",
            )
        _model = load_model(path, device=_DEVICE)
        _n_params = sum(p.numel() for p in _model.parameters())
    return _model


@app.get("/healthz")
def healthz() -> dict:
    try:
        _ensure_model()
        return {
            "status": "ok",
            "checkpoint": _CKPT_PATH,
            "device": _DEVICE,
            "n_params": _n_params,
        }
    except HTTPException as exc:
        return {"status": "degraded", "detail": exc.detail}


@app.post("/layout/correct", response_model=CorrectResponse)
def correct(body: CorrectRequest) -> CorrectResponse:
    model = _ensure_model()
    source, predicted = correct_layout_svg(model, body.svg)
    if not source.nodes:
        raise HTTPException(422, "SVG produced an empty scene graph")
    n_protected = sum(1 for n in source.nodes if n.is_protected)
    return CorrectResponse(
        source=source.to_dict(),
        predicted=predicted.to_dict(),
        n_nodes=len(source.nodes),
        n_protected_pinned=n_protected,
        warnings=[],
    )
