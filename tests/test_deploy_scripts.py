"""Guards on the shell that actually launches the bot (2026-09-28).

Run with:  python tests/test_deploy_scripts.py

These are here because the worst outage of the project so far was not a
strategy bug or a broker error. It was `start.sh` calling bare `python3`:
the system interpreter has none of the bot's packages, so arm.py died with
ModuleNotFoundError, systemd restarted it five times, and both the paper and
the live session for 2026-09-28 were lost. The live bot WAS armed -- the
launcher just could not read the arm state, and treated the crash as
"not armed".

Nothing in the Python test suite could have caught that, so these read the
shell.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
START = (ROOT / "deploy" / "start.sh").read_text(encoding="utf-8")


def _code_lines(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(line)
    return out


def test_start_sh_never_runs_bare_python():
    """The interpreter must come from the venv, not from $PATH."""
    bad = [
        ln for ln in _code_lines(START)
        if re.search(r"(^|[^/\w\"'$])python3?\s+(-u\s+)?(arm|run)\.py", ln)
    ]
    assert not bad, "start.sh invokes a bare interpreter:\n  " + "\n  ".join(bad)
    print("PASS test_start_sh_never_runs_bare_python")


def test_start_sh_resolves_the_venv():
    assert ".venv/bin/python" in START, "start.sh must resolve the venv python"
    assert 'PY="' in START and '"$PY"' in START, "resolved interpreter must be reused"
    print("PASS test_start_sh_resolves_the_venv")


def test_start_sh_checks_the_environment_before_trusting_it():
    """A broken venv must abort loudly, never look like an idle day."""
    assert "import orb_bot.state" in START, (
        "start.sh must verify the interpreter can load the bot before using it"
    )
    assert "BROKEN ENV" in START, "a broken environment must page you"
    print("PASS test_start_sh_checks_the_environment_before_trusting_it")


def test_live_distinguishes_unarmed_from_broken():
    """'not armed' and 'the check crashed' are different states.

    Conflating them is precisely what lost 2026-09-28's live session without
    an alert: the arm existed, arm.py crashed, and the launcher reported
    'not armed' and exited 0."""
    assert "arm check FAILED" in START, (
        "a failed arm check must alert, not fall through to 'not armed'"
    )
    live = START[START.index('"$MODE" == "live"'):]
    assert live.index("arm check FAILED") < live.index("not armed"), (
        "the failure branch must come before the unarmed branch"
    )
    print("PASS test_live_distinguishes_unarmed_from_broken")


def test_vps_setup_does_not_patch_tracked_files():
    """An install-time sed on a tracked file makes every future pull conflict,
    and resolving that conflict silently reverts the patch."""
    setup = (ROOT / "deploy" / "vps-setup.sh").read_text(encoding="utf-8")
    bad = [ln for ln in _code_lines(setup)
           if "sed -i" in ln and "start.sh" in ln]
    assert not bad, "vps-setup.sh patches a tracked file:\n  " + "\n  ".join(bad)
    print("PASS test_vps_setup_does_not_patch_tracked_files")


def run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\nAll {len(fns)} checks passed.")


if __name__ == "__main__":
    run_all()
