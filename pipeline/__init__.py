"""Agentic market-data and ops pipeline for the ORB bot.

Three layers, built 2026-10-25:

  store.py    unified on-disk cache (replaces .sweep_cache/.replay_cache/.qqq_cache)
  health.py   ops checks - what invariants.py is structurally blind to
  snapshot.py per-session snapshot of what the screener saw, for the dashboard

Design rule: NOTHING in here is allowed to influence a trade decision. This is
research and operations tooling. An agent in the trade decision cannot be
backtested (a model reading news for a past date already knows what happened
next), so the pipeline deliberately stops at the research/ops boundary.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATE = REPO / "state"
LOGS = REPO / "logs"
CACHE = REPO / ".pipeline_cache"

__all__ = ["REPO", "STATE", "LOGS", "CACHE"]
