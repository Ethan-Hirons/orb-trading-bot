#!/usr/bin/env python3
"""Ops health report for the ORB bot.

    python ops_report.py                 # human report, exit 1 on any FAIL
    python ops_report.py --json          # machine-readable
    python ops_report.py --root live     # check the other instance
    python ops_report.py --notify        # pipe failures to deploy/notify.sh

Complements invariants.py rather than replacing it: invariants validates
sessions it can see, this checks the seams between artefacts. Run both.
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline.health import render, run_all, to_json  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None,
                    help="repo root to check (default: this one)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true",
                    help="push failures via deploy/notify.sh")
    a = ap.parse_args()

    root = Path(a.root).resolve() if a.root else Path(__file__).resolve().parent
    checks = run_all(root)
    out = to_json(checks) if a.json else render(checks, root)
    print(out)

    failures = [c for c in checks if c.failed]
    if a.notify and failures:
        msg = "ORB ops FAIL: " + "; ".join(f"{c.name}: {c.summary}"
                                           for c in failures)
        script = root / "deploy" / "notify.sh"
        if script.exists():
            try:
                subprocess.run(["bash", str(script), msg], timeout=30,
                               check=False)
            except Exception as e:
                print(f"(notify failed: {e})", file=sys.stderr)
        else:
            print("(no deploy/notify.sh; skipping push)", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
