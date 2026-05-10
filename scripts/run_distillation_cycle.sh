#!/usr/bin/env bash
# End-to-end Sevim distillation cycle.  Pulls fresh telemetry, exports
# JSONL training data, retrains the Qwen LoRA, evaluates against the
# incumbent, and (when the new variant beats the incumbent by the
# configured delta) promotes it by writing a "winning_lora.json"
# pointer to S3 that the vLLM serve job reads on startup.
#
# Where this runs: the user's local 5090 (cheapest path).  AWS
# credentials are needed only for the S3 push/pull steps.
#
# Usage:
#   scripts/run_distillation_cycle.sh
#
# Env (typical defaults shown):
#   SEVIM_TELEMETRY_DB              postgresql://... (production) or
#                                   ~/.local/share/sevim/telemetry.db (dev)
#   SEVIM_EXPORT_S3_BUCKET          sevim-prod-training       (export upload)
#   SEVIM_LORA_S3_BUCKET            sevim-prod-loras           (artefact upload)
#   SEVIM_LORA_INCUMBENT            qwen_lora_v2               (the model to beat)
#   SEVIM_LORA_BASE_MODEL           Qwen/Qwen2.5-7B-Instruct
#   SEVIM_PROMOTION_DELTA           1.0   (judge delta required to swap)
#   SEVIM_DISTILL_EPOCHS            3
#   SEVIM_DISTILL_RANK              8
#   SEVIM_DISTILL_ALPHA             16
#   SEVIM_DISTILL_DRY_RUN           1     (skip train + judge — just test wiring)

set -euo pipefail
cd "$(dirname "$0")/.."

# --- bootstrap ----------------------------------------------------------
if [ -f .env ]; then set -a; . ./.env; set +a; fi

PY=".venv/bin/python"
AWS=".venv/bin/aws"
[ -x "$PY" ] || { echo "no venv at .venv — run 'uv sync'"; exit 2; }
[ -x "$AWS" ] || { echo "awscli missing — uv pip install awscli"; exit 2; }

RUN_TS="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="distill/runs/${RUN_TS}"
mkdir -p "$RUN_DIR"

LORA_INCUMBENT="${SEVIM_LORA_INCUMBENT:-qwen_lora_v2}"
PROMOTION_DELTA="${SEVIM_PROMOTION_DELTA:-1.0}"
EXPORT_BUCKET="${SEVIM_EXPORT_S3_BUCKET:-}"
LORA_BUCKET="${SEVIM_LORA_S3_BUCKET:-}"
DRY_RUN="${SEVIM_DISTILL_DRY_RUN:-0}"

log() { printf '\n[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }

# --- 1. export training data -------------------------------------------
log "1/5 export training data → $RUN_DIR"

# We compute --since from the timestamp of the previous run so the
# corpus grows incrementally.  First run uses 0.
SINCE_FILE="distill/last_export_ts"
SINCE_TS=$(cat "$SINCE_FILE" 2>/dev/null || echo 0)
log "  exporting rows with timestamp > ${SINCE_TS}"

for MODE in sft-clean sft-corrected dpo-pairs; do
    OUT="${RUN_DIR}/${MODE}.jsonl"
    EXTRA=()
    if [ -n "$EXPORT_BUCKET" ]; then
        EXTRA+=(--s3-bucket "$EXPORT_BUCKET" --s3-prefix "training/${RUN_TS}/")
    fi
    "$PY" -m studio.export_finetune \
        --mode "$MODE" \
        --out "$OUT" \
        --since "$SINCE_TS" \
        "${EXTRA[@]}" \
        > "${RUN_DIR}/export-${MODE}.json"
done

# Update cursor — newest of the three latest_ts.
NEW_TS=$("$PY" -c '
import json, sys, glob
mx = 0
for p in sys.argv[1:]:
    with open(p) as f:
        d = json.load(f)
    mx = max(mx, d.get("last_ts", 0))
print(mx)
' "${RUN_DIR}"/export-*.json)
echo "$NEW_TS" > "$SINCE_FILE"
log "  cursor advanced to ${NEW_TS}"

# Skip training when there's nothing new (cron-friendly).
CLEAN_KEPT=$("$PY" -c "import json;print(json.load(open('${RUN_DIR}/export-sft-clean.json'))['kept'])")
if [ "$CLEAN_KEPT" -lt 50 ] && [ "$DRY_RUN" != "1" ]; then
    log "only $CLEAN_KEPT new clean turns — skipping retrain (need ≥50)"
    cat > "${RUN_DIR}/manifest.json" <<EOF
{"ts":"${RUN_TS}","status":"skipped","reason":"insufficient_new_data","clean_kept":${CLEAN_KEPT}}
EOF
    exit 0
fi

# --- 2. train the new LoRA ---------------------------------------------
log "2/5 train new LoRA"
LORA_OUT="${RUN_DIR}/lora"
if [ "$DRY_RUN" = "1" ]; then
    log "  DRY_RUN=1 — skipping actual training"
    mkdir -p "$LORA_OUT"
    echo "dry-run placeholder" > "${LORA_OUT}/README"
else
    "$PY" scripts/train_lora.py \
        --dataset "${RUN_DIR}/sft-clean.jsonl" \
        --out "$LORA_OUT" \
        --epochs "${SEVIM_DISTILL_EPOCHS:-3}" \
        --rank "${SEVIM_DISTILL_RANK:-8}" \
        --alpha "${SEVIM_DISTILL_ALPHA:-16}" \
        2>&1 | tee "${RUN_DIR}/train.log"
fi

# --- 3. push artefact to S3 --------------------------------------------
log "3/5 push LoRA artefact to S3"
LORA_S3_PREFIX="loras/${RUN_TS}/"
if [ -n "$LORA_BUCKET" ]; then
    "$AWS" s3 sync "$LORA_OUT/" "s3://${LORA_BUCKET}/${LORA_S3_PREFIX}" --quiet
    log "  uploaded to s3://${LORA_BUCKET}/${LORA_S3_PREFIX}"
else
    log "  SEVIM_LORA_S3_BUCKET unset — skipping push"
fi

# --- 4. evaluate vs. incumbent -----------------------------------------
log "4/5 evaluate vs incumbent ${LORA_INCUMBENT}"
JUDGE_TAG="cycle_${RUN_TS}"
if [ "$DRY_RUN" = "1" ]; then
    NEW_SCORE=18.0; INCUMBENT_SCORE=17.8
    log "  DRY_RUN=1 — synthetic scores new=$NEW_SCORE incumbent=$INCUMBENT_SCORE"
else
    "$PY" scripts/judge_lora_variants.py \
        --models "$LORA_INCUMBENT" "qwen_lora_${RUN_TS}" \
        --tag "$JUDGE_TAG" \
        2>&1 | tee "${RUN_DIR}/judge.log"
    # judge_lora_variants writes a CSV under /tmp/sevim_compare/<tag>.
    # Pull the avg-of-axes score per model.
    JUDGE_CSV="/tmp/sevim_compare/${JUDGE_TAG}.csv"
    if [ ! -f "$JUDGE_CSV" ]; then
        log "ERROR: judge CSV not found at $JUDGE_CSV"
        exit 1
    fi
    NEW_SCORE=$("$PY" -c "
import csv, sys
with open(sys.argv[1]) as f:
    rows = list(csv.DictReader(f))
new = [r for r in rows if r.get('model') == sys.argv[2]]
if not new:
    print(0); sys.exit(0)
axes = ['visual_clarity', 'math_correctness', 'completeness']
vals = [float(new[0].get(a, 0)) for a in axes]
print(sum(vals))
" "$JUDGE_CSV" "qwen_lora_${RUN_TS}")
    INCUMBENT_SCORE=$("$PY" -c "
import csv, sys
with open(sys.argv[1]) as f:
    rows = list(csv.DictReader(f))
inc = [r for r in rows if r.get('model') == sys.argv[2]]
if not inc:
    print(0); sys.exit(0)
axes = ['visual_clarity', 'math_correctness', 'completeness']
vals = [float(inc[0].get(a, 0)) for a in axes]
print(sum(vals))
" "$JUDGE_CSV" "$LORA_INCUMBENT")
fi

# --- 5. auto-promote ---------------------------------------------------
log "5/5 promotion decision  new=${NEW_SCORE}  incumbent=${INCUMBENT_SCORE}  delta_required=${PROMOTION_DELTA}"
DELTA=$("$PY" -c "print(${NEW_SCORE} - ${INCUMBENT_SCORE})")
PROMOTED=0
if [ "$("$PY" -c "print(1 if ${DELTA} >= ${PROMOTION_DELTA} else 0)")" = "1" ]; then
    PROMOTED=1
    cat > "${RUN_DIR}/winning_lora.json" <<EOF
{
  "s3_bucket": "${LORA_BUCKET}",
  "s3_prefix": "${LORA_S3_PREFIX}",
  "ts": "${RUN_TS}",
  "score": ${NEW_SCORE},
  "incumbent_score": ${INCUMBENT_SCORE},
  "delta": ${DELTA},
  "incumbent": "${LORA_INCUMBENT}"
}
EOF
    if [ -n "$LORA_BUCKET" ]; then
        "$AWS" s3 cp "${RUN_DIR}/winning_lora.json" \
            "s3://${LORA_BUCKET}/winning_lora.json" --quiet
        log "  PROMOTED — wrote s3://${LORA_BUCKET}/winning_lora.json"
    else
        log "  PROMOTED locally — no S3 bucket configured"
    fi
else
    log "  NOT PROMOTED — delta ${DELTA} < required ${PROMOTION_DELTA}"
fi

# --- manifest ---------------------------------------------------------
cat > "${RUN_DIR}/manifest.json" <<EOF
{
  "ts": "${RUN_TS}",
  "status": "completed",
  "promoted": ${PROMOTED},
  "scores": {"new": ${NEW_SCORE}, "incumbent": ${INCUMBENT_SCORE}, "delta": ${DELTA}},
  "lora_s3": "s3://${LORA_BUCKET}/${LORA_S3_PREFIX}",
  "incumbent": "${LORA_INCUMBENT}",
  "promotion_delta_required": ${PROMOTION_DELTA},
  "clean_kept": ${CLEAN_KEPT}
}
EOF
log "manifest written to ${RUN_DIR}/manifest.json"
exit 0
