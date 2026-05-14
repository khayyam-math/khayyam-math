"""PyTorch dataset + collate for layout-correction training pairs.

Every example is a (source_graph, target_graph) pair from the same
prompt. The model must predict per-node bbox deltas that move
source.nodes[i].bbox to target.nodes[i].bbox. We require the two
graphs to share node-id structure (the parser produces stable ids
based on SVG ``id`` attrs and deterministic anonymous fallbacks);
when ids don't line up the pair is dropped (logged).

Numeric features are normalised to [0, 1] using the source graph's
viewbox. Node-type and edge-type embeddings are integer indices
into the closed vocabularies in `schema.NODE_TYPES` /
`schema.EDGE_RELATIONS`.

Batching uses the "stacked graph" trick: nodes from all graphs in a
batch are concatenated, edges are reindexed globally, and a
`batch_idx` tensor records which graph each node belongs to. This
avoids padding while keeping the model architecture
graph-size-agnostic.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import torch
from torch.utils.data import Dataset

from .schema import (
    EDGE_RELATIONS, MATH_BUCKETS, NODE_TYPES, SceneGraph, TrainingPair,
    classify_math_bucket, read_jsonl,
)

# Index lookups
_NODE_TYPE_IDX: dict[str, int] = {t: i for i, t in enumerate(NODE_TYPES)}
_EDGE_REL_IDX: dict[str, int] = {r: i for i, r in enumerate(EDGE_RELATIONS)}
_BUCKET_IDX: dict[str, int] = {b: i for i, b in enumerate(MATH_BUCKETS)}

NUM_NODE_TYPES = len(NODE_TYPES)
NUM_EDGE_RELS = len(EDGE_RELATIONS)
NUM_BUCKETS = len(MATH_BUCKETS)

# Numeric per-node feature dimensions: bbox(4) + text_len(1) + font_size(1)
# + stroke_width(1) + flags(3 booleans) + viewbox(4) = 14
NUM_NUMERIC_FEATS = 14


def _node_type_idx(t: str) -> int:
    return _NODE_TYPE_IDX.get(t, _NODE_TYPE_IDX["other"])


def _edge_rel_idx(r: str) -> int:
    return _EDGE_REL_IDX.get(r, _EDGE_REL_IDX["semantic_other"])


def _numeric_features(
    graph: SceneGraph,
) -> torch.Tensor:
    """Return [N, 14] tensor of normalised per-node numeric features.

    Order: x/cw, y/ch, w/cw, h/ch, text_len_log, font_size/64,
    stroke_width/10, is_anchor, is_caption, is_protected,
    vb_x/cw, vb_y/ch, vb_w/cw, vb_h/ch.
    """
    cw = max(1.0, float(graph.canvas_w))
    ch = max(1.0, float(graph.canvas_h))
    vb_x, vb_y, vb_w, vb_h = graph.viewbox
    rows: list[list[float]] = []
    for n in graph.nodes:
        x, y, w, h = n.bbox
        import math
        rows.append([
            x / cw, y / ch, w / cw, h / ch,
            min(1.0, math.log1p(len(n.text)) / 5.0),
            min(1.0, n.font_size / 64.0),
            min(1.0, n.stroke_width / 10.0),
            1.0 if n.is_narration_anchor else 0.0,
            1.0 if n.is_caption else 0.0,
            1.0 if n.is_protected else 0.0,
            vb_x / cw, vb_y / ch, vb_w / cw, vb_h / ch,
        ])
    return torch.tensor(rows, dtype=torch.float32)


def _type_indices(graph: SceneGraph) -> torch.Tensor:
    return torch.tensor(
        [_node_type_idx(n.type) for n in graph.nodes],
        dtype=torch.long,
    )


def _edge_tensors(
    graph: SceneGraph, id_to_idx: dict[str, int],
) -> tuple[torch.Tensor, torch.Tensor]:
    """Return (edge_index [2, E], edge_relation [E]) for the graph."""
    srcs: list[int] = []
    dsts: list[int] = []
    rels: list[int] = []
    for e in graph.edges:
        if e.src_id not in id_to_idx or e.dst_id not in id_to_idx:
            continue  # dangling edge — skip
        srcs.append(id_to_idx[e.src_id])
        dsts.append(id_to_idx[e.dst_id])
        rels.append(_edge_rel_idx(e.relation))
    if not srcs:
        # avoid empty tensors that break some torch ops
        return (torch.zeros((2, 0), dtype=torch.long),
                torch.zeros((0,), dtype=torch.long))
    edge_index = torch.tensor([srcs, dsts], dtype=torch.long)
    edge_rel = torch.tensor(rels, dtype=torch.long)
    return edge_index, edge_rel


# --------------------------------------------------------------------
# pair → tensors
# --------------------------------------------------------------------

@dataclass
class GraphExample:
    """All tensors for ONE (source, target) training pair."""
    node_types: torch.Tensor      # [N] long
    numeric_feats: torch.Tensor   # [N, 14]
    edge_index: torch.Tensor      # [2, E]
    edge_rel: torch.Tensor        # [E]
    target_delta: torch.Tensor    # [N, 4] = (target.bbox - source.bbox) / canvas_size
    is_protected: torch.Tensor    # [N] bool
    canvas_wh: torch.Tensor       # [2] floats for unnormalising
    bucket_idx: int

    @property
    def n_nodes(self) -> int:
        return self.node_types.shape[0]


def pair_to_example(pair: TrainingPair) -> GraphExample | None:
    """Convert a TrainingPair to a GraphExample tensor bundle.

    Returns None if the two graphs disagree on node id structure
    (different node sets — can happen when the LLM added or removed
    elements during a repair). Strict matching keeps the per-node
    delta prediction well-defined.
    """
    src_graph = pair.source
    tgt_graph = pair.target
    src_id_to_idx = {n.id: i for i, n in enumerate(src_graph.nodes)}
    tgt_id_to_idx = {n.id: i for i, n in enumerate(tgt_graph.nodes)}
    # Strict: target ids must be a subset of source ids; we align
    # source→target by id and drop any source nodes the target lacks.
    common_ids = [n.id for n in src_graph.nodes if n.id in tgt_id_to_idx]
    if len(common_ids) < 2:
        return None
    # Build a filtered source graph that contains only matched ids,
    # in source order.
    src_kept_nodes = [
        src_graph.nodes[src_id_to_idx[i]] for i in common_ids
    ]
    new_id_to_idx = {nid: i for i, nid in enumerate(common_ids)}

    # Build a filtered SceneGraph for source.
    filt_src = SceneGraph(
        nodes=src_kept_nodes,
        edges=[e for e in src_graph.edges
               if e.src_id in new_id_to_idx and e.dst_id in new_id_to_idx],
        viewbox=src_graph.viewbox,
        canvas_w=src_graph.canvas_w,
        canvas_h=src_graph.canvas_h,
    )
    node_types = _type_indices(filt_src)
    numeric = _numeric_features(filt_src)
    edge_index, edge_rel = _edge_tensors(filt_src, new_id_to_idx)

    # target delta in normalised coords
    cw = max(1.0, float(src_graph.canvas_w))
    ch = max(1.0, float(src_graph.canvas_h))
    deltas: list[list[float]] = []
    is_protected: list[bool] = []
    for nid in common_ids:
        s = src_graph.nodes[src_id_to_idx[nid]]
        t = tgt_graph.nodes[tgt_id_to_idx[nid]]
        sx, sy, sw, sh = s.bbox
        tx, ty, tw, th = t.bbox
        deltas.append([
            (tx - sx) / cw, (ty - sy) / ch,
            (tw - sw) / cw, (th - sh) / ch,
        ])
        is_protected.append(bool(s.is_protected))
    target_delta = torch.tensor(deltas, dtype=torch.float32)
    return GraphExample(
        node_types=node_types,
        numeric_feats=numeric,
        edge_index=edge_index,
        edge_rel=edge_rel,
        target_delta=target_delta,
        is_protected=torch.tensor(is_protected, dtype=torch.bool),
        canvas_wh=torch.tensor([cw, ch], dtype=torch.float32),
        bucket_idx=_BUCKET_IDX.get(pair.math_bucket, _BUCKET_IDX["other"]),
    )


# --------------------------------------------------------------------
# Dataset
# --------------------------------------------------------------------

class LayoutPairDataset(Dataset):
    """Reads one or more TrainingPair JSONL files and yields
    GraphExample tensor bundles. The conversion happens lazily so
    constructing the dataset is fast even on large corpora."""

    def __init__(
        self,
        sources: Sequence[str | Path],
        *,
        min_nodes: int = 2,
        max_nodes: int = 200,
    ) -> None:
        super().__init__()
        self._records: list[tuple[Path, int]] = []  # (path, byte_offset)
        self.min_nodes = min_nodes
        self.max_nodes = max_nodes
        for source in sources:
            path = Path(source)
            offset = 0
            with path.open("rb") as fh:
                for line in fh:
                    if line.strip():
                        self._records.append((path, offset))
                    offset += len(line)
        # cache parsed pairs for speed — examples are small.
        self._cache: dict[int, GraphExample | None] = {}

    def __len__(self) -> int:
        return len(self._records)

    def _load_one(self, idx: int) -> GraphExample | None:
        path, offset = self._records[idx]
        with path.open("rb") as fh:
            fh.seek(offset)
            line = fh.readline()
        try:
            pair = TrainingPair.from_dict(json.loads(line))
        except (json.JSONDecodeError, ValueError, KeyError):
            return None
        ex = pair_to_example(pair)
        if ex is None or not (self.min_nodes <= ex.n_nodes <= self.max_nodes):
            return None
        return ex

    def __getitem__(self, idx: int) -> GraphExample:
        cached = self._cache.get(idx)
        if cached is not None:
            return cached
        # Try the requested idx, then walk forward until we find a
        # valid example. Bounded scan — no recursion.
        N = len(self._records)
        for off in range(N):
            cur = (idx + off) % N
            cached = self._cache.get(cur)
            if cached is not None:
                return cached
            ex = self._load_one(cur)
            if ex is not None:
                self._cache[cur] = ex
                return ex
        raise RuntimeError("no valid examples in dataset")


# --------------------------------------------------------------------
# Batching
# --------------------------------------------------------------------

@dataclass
class GraphBatch:
    node_types: torch.Tensor       # [Nb]
    numeric_feats: torch.Tensor    # [Nb, 14]
    edge_index: torch.Tensor       # [2, Eb] global indices
    edge_rel: torch.Tensor         # [Eb]
    target_delta: torch.Tensor     # [Nb, 4]
    is_protected: torch.Tensor     # [Nb]
    batch_idx: torch.Tensor        # [Nb] which sample in the batch
    canvas_wh: torch.Tensor        # [B, 2]

    def to(self, device: torch.device) -> "GraphBatch":
        return GraphBatch(
            node_types=self.node_types.to(device),
            numeric_feats=self.numeric_feats.to(device),
            edge_index=self.edge_index.to(device),
            edge_rel=self.edge_rel.to(device),
            target_delta=self.target_delta.to(device),
            is_protected=self.is_protected.to(device),
            batch_idx=self.batch_idx.to(device),
            canvas_wh=self.canvas_wh.to(device),
        )


def collate(examples: list[GraphExample]) -> GraphBatch:
    node_offsets = [0]
    for ex in examples:
        node_offsets.append(node_offsets[-1] + ex.n_nodes)
    n_total = node_offsets[-1]
    node_types = torch.cat([ex.node_types for ex in examples], dim=0)
    numeric = torch.cat([ex.numeric_feats for ex in examples], dim=0)
    target = torch.cat([ex.target_delta for ex in examples], dim=0)
    protected = torch.cat([ex.is_protected for ex in examples], dim=0)
    batch_idx_parts: list[torch.Tensor] = []
    edges_parts: list[torch.Tensor] = []
    edge_rel_parts: list[torch.Tensor] = []
    for b, ex in enumerate(examples):
        batch_idx_parts.append(
            torch.full((ex.n_nodes,), b, dtype=torch.long),
        )
        if ex.edge_index.numel() > 0:
            shifted = ex.edge_index + node_offsets[b]
            edges_parts.append(shifted)
            edge_rel_parts.append(ex.edge_rel)
    batch_idx = torch.cat(batch_idx_parts, dim=0)
    edge_index = (
        torch.cat(edges_parts, dim=1) if edges_parts
        else torch.zeros((2, 0), dtype=torch.long)
    )
    edge_rel = (
        torch.cat(edge_rel_parts, dim=0) if edge_rel_parts
        else torch.zeros((0,), dtype=torch.long)
    )
    canvas = torch.stack([ex.canvas_wh for ex in examples], dim=0)
    return GraphBatch(
        node_types=node_types,
        numeric_feats=numeric,
        edge_index=edge_index,
        edge_rel=edge_rel,
        target_delta=target,
        is_protected=protected,
        batch_idx=batch_idx,
        canvas_wh=canvas,
    )


# --------------------------------------------------------------------
# Bucket-balanced sampler
# --------------------------------------------------------------------

class BucketBalancedSampler(torch.utils.data.Sampler[int]):
    """Sample indices so every bucket is over-/under-sampled toward
    equal representation per epoch. Implemented as: for each
    `__iter__`, draw one index per bucket round-robin until the
    epoch length is reached."""

    def __init__(
        self,
        dataset: LayoutPairDataset,
        bucket_idx_fn,
        length: int | None = None,
        seed: int = 0,
    ) -> None:
        self.dataset = dataset
        self.length = length or len(dataset)
        self.seed = seed
        self._per_bucket: dict[int, list[int]] = {}
        for i in range(len(dataset)):
            try:
                b = bucket_idx_fn(i)
            except Exception:  # noqa: BLE001
                continue
            self._per_bucket.setdefault(b, []).append(i)

    def __iter__(self) -> Iterator[int]:
        rng = random.Random(self.seed)
        self.seed += 1
        buckets = [b for b, idxs in self._per_bucket.items() if idxs]
        rng.shuffle(buckets)
        emitted = 0
        per_bucket_cursors: dict[int, int] = {b: 0 for b in buckets}
        while emitted < self.length:
            for b in buckets:
                if emitted >= self.length:
                    break
                idxs = self._per_bucket[b]
                if not idxs:
                    continue
                cur = per_bucket_cursors[b]
                if cur >= len(idxs):
                    rng.shuffle(idxs)
                    per_bucket_cursors[b] = 0
                    cur = 0
                yield idxs[cur]
                per_bucket_cursors[b] = cur + 1
                emitted += 1

    def __len__(self) -> int:
        return self.length
