"""Neural layout-correction subsystem.

See PLAN.md for the full design. Public API is intentionally narrow:

- `schema` — dataclasses for the training-pair record format.
- `svg_to_graph` — pure SVG → SceneGraph parser.
- `exporter` — express-loop result → TrainingPair list.

The model code lives under ``studio.neural_layout.models`` once
Phase B starts.
"""
from __future__ import annotations

from . import schema, svg_to_graph, exporter  # noqa: F401

__all__ = ["schema", "svg_to_graph", "exporter"]
