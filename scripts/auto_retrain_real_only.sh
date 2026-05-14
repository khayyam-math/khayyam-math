#!/usr/bin/env bash
# Wait for the background corpus_v1 build to finish, then retrain
# the GNN baseline + LayoutDM on REAL-ONLY data (starter +
# corpus_v1, no synthetic perturbations), and run per-bucket eval.
#
# Resumable: if killed, just re-run; everything is idempotent.

set -euo pipefail
cd "$(dirname "$0")/.."

CORPUS_PID="${1:-190901}"
OUT_LOG="/tmp/auto_retrain.log"

echo "$(date -Iseconds) — waiting for corpus_v1 build PID $CORPUS_PID" | tee -a "$OUT_LOG"

# Poll until the corpus build exits.
while kill -0 "$CORPUS_PID" 2>/dev/null; do
    pairs=$(wc -l < data/neural_layout/corpus_v1.jsonl 2>/dev/null || echo 0)
    echo "$(date -Iseconds) — still running: $pairs pairs" | tee -a "$OUT_LOG"
    sleep 300
done

final=$(wc -l < data/neural_layout/corpus_v1.jsonl)
echo "$(date -Iseconds) — corpus done, $final pairs" | tee -a "$OUT_LOG"

# Re-bucket (in case keyword classifier was improved).
echo "$(date -Iseconds) — re-bucketing corpus_v1" | tee -a "$OUT_LOG"
.venv/bin/python scripts/rebucket_pairs.py \
    --in data/neural_layout/corpus_v1.jsonl \
    --out data/neural_layout/corpus_v1.jsonl \
    >> "$OUT_LOG" 2>&1

# Train GNN baseline on REAL-ONLY (starter + corpus_v1).
echo "$(date -Iseconds) — training GNN baseline (real only)" | tee -a "$OUT_LOG"
.venv/bin/python -m studio.neural_layout.train_gnn \
    --data data/neural_layout/starter_pairs.jsonl \
    --data data/neural_layout/corpus_v1.jsonl \
    --out runs/gnn_real_v2 \
    --epochs 80 --batch-size 32 --num-workers 0 \
    --lr 3e-4 --delta-weight 2.0 \
    --model-size default \
    >> "$OUT_LOG" 2>&1

# Train LayoutDM on REAL-ONLY (target distribution = real iteration
# accepted versions, much higher-quality data than synthetic).
echo "$(date -Iseconds) — training LayoutDM (real only)" | tee -a "$OUT_LOG"
.venv/bin/python -m studio.neural_layout.train_layoutdm \
    --data data/neural_layout/starter_pairs.jsonl \
    --data data/neural_layout/corpus_v1.jsonl \
    --out runs/layoutdm_real_v1 \
    --epochs 120 --batch-size 32 --num-workers 0 \
    --lr 3e-4 --T 100 --model-size default \
    >> "$OUT_LOG" 2>&1

# Eval both.
echo "$(date -Iseconds) — eval GNN real v2" | tee -a "$OUT_LOG"
.venv/bin/python scripts/eval_gnn.py \
    --ckpt runs/gnn_real_v2/best.pt \
    --data data/neural_layout/starter_pairs.jsonl \
    --data data/neural_layout/corpus_v1.jsonl \
    --val-frac 0.1 \
    >> "$OUT_LOG" 2>&1

echo "$(date -Iseconds) — DONE" | tee -a "$OUT_LOG"
echo
echo "=== final summary ==="
echo "corpus_v1 pairs: $final"
echo "checkpoints:"
ls -la runs/gnn_real_v2/*.pt runs/layoutdm_real_v1/*.pt 2>&1
