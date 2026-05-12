"""After fine-tuning completes, wire the new model id into the
catalog AND set SEVIM_FORCE_ACTIVE_MODEL so the next cdk deploy
flips production traffic.

Usage:
    .venv/bin/python scripts/promote_finetune.py \\
        --model-id ft:gpt-4o-mini-2024-07-18:org:khayyam-v1:abc

Produces git diffs on:
  * studio/app.py — adds a MODEL_CATALOG entry with available=True,
    default=False.
  * infra/sevim_stack.py — adds SEVIM_FORCE_ACTIVE_MODEL=<id> to
    the Fargate task env block.

Prints a final `git commit` command at the end so the operator can
review the diff before committing.  Does NOT auto-commit or deploy.
"""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_PY = ROOT / "studio" / "app.py"
STACK_PY = ROOT / "infra" / "sevim_stack.py"


def add_to_model_catalog(model_id: str, label: str) -> bool:
    """Insert a new dict entry into MODEL_CATALOG.  Returns True iff
    a change was written."""
    src = APP_PY.read_text()
    if model_id in src:
        print(f"  [{APP_PY.name}] {model_id} already present — no change")
        return False
    # Find MODEL_CATALOG = [ ... ] and append before the closing ].
    m = re.search(
        r"(MODEL_CATALOG\s*:\s*list\[[^\]]+\]\s*=\s*\[)(.*?)(\n\])",
        src, re.S,
    )
    if not m:
        print(f"  ! couldn't find MODEL_CATALOG block in {APP_PY}",
              file=sys.stderr)
        return False
    head, body, tail = m.group(1), m.group(2), m.group(3)
    entry = (
        '\n    {"id": ' + repr(model_id) + ',\n'
        '     "label": ' + repr(label) + ',\n'
        '     "default": False, "available": True},'
    )
    new_block = head + body + entry + tail
    new_src = src[:m.start()] + new_block + src[m.end():]
    APP_PY.write_text(new_src)
    print(f"  [{APP_PY.name}] added MODEL_CATALOG entry for {model_id}")
    return True


def set_force_active_model(model_id: str) -> bool:
    """Inject SEVIM_FORCE_ACTIVE_MODEL=<id> into the CDK env_vars
    dict.  Returns True iff a change was written."""
    src = STACK_PY.read_text()
    pat = re.compile(r'"SEVIM_FORCE_ACTIVE_MODEL"\s*:\s*"[^"]*"')
    if pat.search(src):
        # Already present — update.
        new_src = pat.sub(f'"SEVIM_FORCE_ACTIVE_MODEL": "{model_id}"', src,
                          count=1)
    else:
        # Find env_vars: dict[str, str] = { ... } and add inside.
        m = re.search(r'(env_vars\s*:\s*dict\[str,\s*str\]\s*=\s*\{)',
                      src, re.S)
        if not m:
            print(f"  ! couldn't find env_vars block in {STACK_PY}",
                  file=sys.stderr)
            return False
        insert_at = m.end()
        new_src = (src[:insert_at]
                   + f'\n            "SEVIM_FORCE_ACTIVE_MODEL": "{model_id}",'
                   + src[insert_at:])
    if new_src == src:
        return False
    STACK_PY.write_text(new_src)
    print(f"  [{STACK_PY.name}] SEVIM_FORCE_ACTIVE_MODEL={model_id}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model-id", required=True,
                    help="Fine-tuned model id (ft:...)")
    ap.add_argument("--label", default=None,
                    help="Catalog label (default derived from id)")
    args = ap.parse_args()
    if not args.model_id.startswith("ft:"):
        print(f"ERROR: --model-id must start with 'ft:'", file=sys.stderr)
        return 1
    label = args.label
    if label is None:
        # ft:gpt-4o-mini-2024-07-18:org:suffix:xyz
        parts = args.model_id.split(":")
        if len(parts) >= 4:
            label = f"Khayyam-tuned 4o-mini ({parts[3]})"
        else:
            label = "Khayyam-tuned 4o-mini"

    print(f"=== promote fine-tune ===")
    print(f"  model_id: {args.model_id}")
    print(f"  label:    {label}")
    print()
    added = add_to_model_catalog(args.model_id, label)
    forced = set_force_active_model(args.model_id)
    print()
    if added or forced:
        print("Next steps:")
        print("  git diff studio/app.py infra/sevim_stack.py")
        print(f"  git add studio/app.py infra/sevim_stack.py")
        print(f"  git commit -m 'promote {args.model_id} as active'")
        print("  cd infra && AWS_PROFILE=sevim CDK_DEFAULT_ACCOUNT=REDACTED \\")
        print("    CDK_DEFAULT_REGION=us-east-1 SEVIM_DOMAIN=khayyammath.com \\")
        print("    npx aws-cdk deploy --require-approval never")
        return 0
    print("Nothing to change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
