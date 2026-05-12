"""End-to-end OpenAI fine-tuning runner.

Reads a teacher-corpus JSONL (the same format scripts/generate_teacher_corpus.py
emits), converts it to OpenAI's stricter fine-tuning schema (messages-only),
uploads to /v1/files, creates a fine-tuning job, polls until done, and
prints the resulting model id.

Pipeline:

  1. ``scripts/generate_teacher_corpus.py`` produces JSONL where each
     line is ``{"messages": [system, user, assistant], "meta": {...}}``.
  2. THIS script strips ``meta`` (OpenAI's strict schema rejects extras),
     drops malformed rows, and writes a clean JSONL.
  3. POST clean JSONL to /v1/files with purpose=fine-tune.
  4. POST /v1/fine_tuning/jobs with {training_file, model}.
  5. Poll GET /v1/fine_tuning/jobs/{id} every 30 s until status is
     "succeeded" or "failed".
  6. On success, print the fine-tuned model id (e.g.
     ``ft:gpt-4o-mini-2024-07-18:org:khayyam-math-v1:abc123``).

Usage:

    .venv/bin/python scripts/finetune_openai.py \\
        --in /tmp/teacher_v5.jsonl \\
        --base gpt-4o-mini-2024-07-18 \\
        --suffix khayyam-v1

Output:
    Streamed status messages plus a final line:
        FINETUNED_MODEL_ID=ft:gpt-4o-mini-...
    The id is suitable for direct use in chat/completions calls.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx


OPENAI_API = "https://api.openai.com/v1"


def _key() -> str:
    k = os.environ.get("OPENAI_API_KEY")
    if not k:
        # Try to source from the local .env via service.secrets.
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from service.secrets import bootstrap as _boot
            _boot()
            k = os.environ.get("OPENAI_API_KEY")
        except Exception:
            pass
    if not k:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    return k


def _hdrs() -> dict:
    return {"Authorization": f"Bearer {_key()}"}


def clean_jsonl(in_path: Path, out_path: Path) -> tuple[int, int]:
    """Strip non-OpenAI fields; return (kept, dropped) counts."""
    kept = 0
    dropped = 0
    with in_path.open() as fh, out_path.open("w") as out:
        for ln, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                dropped += 1
                continue
            msgs = row.get("messages")
            if not isinstance(msgs, list) or len(msgs) < 2:
                dropped += 1
                continue
            # OpenAI requires roles in {system, user, assistant, tool, function}.
            cleaned = []
            ok = True
            for m in msgs:
                if (not isinstance(m, dict)
                        or m.get("role") not in ("system", "user", "assistant")
                        or not isinstance(m.get("content"), str)
                        or not m["content"]):
                    ok = False
                    break
                cleaned.append({"role": m["role"], "content": m["content"]})
            if not ok:
                dropped += 1
                continue
            # Final row: messages-only.
            out.write(json.dumps({"messages": cleaned}, ensure_ascii=False) + "\n")
            kept += 1
    return kept, dropped


def upload_file(path: Path) -> str:
    """Upload JSONL to /v1/files with purpose=fine-tune.  Returns file_id."""
    print(f"  uploading {path.name} ({path.stat().st_size:,} bytes) ...",
          flush=True)
    with httpx.Client(timeout=300) as cli:
        with path.open("rb") as fh:
            r = cli.post(
                f"{OPENAI_API}/files",
                headers=_hdrs(),
                files={"file": (path.name, fh, "application/jsonl")},
                data={"purpose": "fine-tune"},
            )
    if r.status_code != 200:
        print(f"  upload FAILED: {r.status_code} {r.text}", file=sys.stderr)
        sys.exit(1)
    fid = r.json()["id"]
    print(f"  uploaded → file_id={fid}", flush=True)
    return fid


def create_job(file_id: str, base_model: str, suffix: str | None) -> str:
    """POST /v1/fine_tuning/jobs.  Returns job_id."""
    payload = {"training_file": file_id, "model": base_model}
    if suffix:
        payload["suffix"] = suffix
    with httpx.Client(timeout=60) as cli:
        r = cli.post(
            f"{OPENAI_API}/fine_tuning/jobs",
            headers={**_hdrs(), "Content-Type": "application/json"},
            json=payload,
        )
    if r.status_code != 200:
        print(f"  job creation FAILED: {r.status_code} {r.text}", file=sys.stderr)
        sys.exit(1)
    jid = r.json()["id"]
    print(f"  job created → job_id={jid}", flush=True)
    return jid


def poll(job_id: str, every_s: int = 30) -> dict:
    """Poll until status is succeeded / failed / cancelled.  Returns job."""
    last_status = None
    while True:
        with httpx.Client(timeout=60) as cli:
            r = cli.get(f"{OPENAI_API}/fine_tuning/jobs/{job_id}",
                        headers=_hdrs())
        if r.status_code != 200:
            print(f"  poll error: {r.status_code} {r.text}", file=sys.stderr)
            time.sleep(every_s)
            continue
        job = r.json()
        status = job.get("status")
        if status != last_status:
            print(f"  [{time.strftime('%H:%M:%S')}] status={status} "
                  f"trained_tokens={job.get('trained_tokens')} "
                  f"finished_at={job.get('finished_at')}",
                  flush=True)
            last_status = status
        if status in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(every_s)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="in_path", type=Path, required=True,
                    help="Teacher-corpus JSONL (output of generate_teacher_corpus.py)")
    ap.add_argument("--base", default="gpt-4o-mini-2024-07-18",
                    help="OpenAI fine-tune base model")
    ap.add_argument("--suffix", default=None,
                    help="Optional suffix for the resulting model id (e.g. 'khayyam-v1')")
    ap.add_argument("--clean-only", action="store_true",
                    help="Just write the cleaned JSONL and exit (no upload, no job)")
    ap.add_argument("--out-clean", type=Path, default=None,
                    help="Where to write the cleaned JSONL (default: <in>.openai.jsonl)")
    args = ap.parse_args(argv)

    clean_path = args.out_clean or args.in_path.with_suffix(".openai.jsonl")
    print(f"=== openai fine-tune runner ===")
    print(f"  input:       {args.in_path}")
    print(f"  cleaned to:  {clean_path}")
    print(f"  base model:  {args.base}")
    print(f"  suffix:      {args.suffix or '(none)'}")
    print()

    kept, dropped = clean_jsonl(args.in_path, clean_path)
    print(f"  cleaned: kept={kept}  dropped={dropped}")
    if kept < 10:
        print(f"  ERROR: OpenAI requires at least 10 examples; got {kept}",
              file=sys.stderr)
        return 1
    if args.clean_only:
        print(f"\n--clean-only set; exiting before upload.")
        return 0

    file_id = upload_file(clean_path)
    job_id = create_job(file_id, args.base, args.suffix)
    print()
    print(f"  Polling job {job_id} every 30 s.  Use Ctrl+C to detach "
          f"(the job keeps running on OpenAI's side).")
    print()
    job = poll(job_id)

    print()
    print(f"=== job complete ===")
    print(f"  status: {job.get('status')}")
    print(f"  trained tokens: {job.get('trained_tokens')}")
    print(f"  finished at: {job.get('finished_at')}")
    if job.get("error"):
        print(f"  error: {job['error']}", file=sys.stderr)
        return 2
    mid = job.get("fine_tuned_model")
    if mid:
        print()
        print(f"FINETUNED_MODEL_ID={mid}")
        print()
        print(f"Next steps:")
        print(f"  1. Add to studio/app.py MODEL_CATALOG:")
        print(f"       {{\"id\": \"{mid}\", \"label\": \"Khayyam-tuned 4o-mini\","
              f" \"default\": False, \"available\": True}}")
        print(f"  2. Deploy:  cd infra && npx aws-cdk deploy")
        print(f"  3. Flip active model via /studio/admin")
        return 0
    return 3


if __name__ == "__main__":
    sys.exit(main())
