"""Pre-flight checks. Refuse to trade rather than trade wrong (v1.18).

Every check here exists because getting it wrong costs real money, and because
the live and paper instances run the same code from the same repo. The single
worst outcome this file prevents is the two instances swapping credentials --
live keys used with paper caps, or paper keys in the directory you believe is
live and are therefore not watching.

Mode comes from the ORB_MODE environment variable, set by deploy/start.sh:

    ORB_MODE=paper   (default)   ORB_MODE=live

A live session must ALSO have state/LIVE_CONFIRMED present. That file is
created by hand, once, by a human who has read LIVE-GO.md. Nothing in this
repo creates it.
"""

from __future__ import annotations

import os
from pathlib import Path

CONFIRM_FILE = Path("state/LIVE_CONFIRMED")


def mode() -> str:
    return "live" if os.environ.get("ORB_MODE", "paper").lower() == "live" else "paper"


def _is_true(v) -> bool:
    return bool(v) and str(v).lower() not in ("false", "0", "none", "")


def check(cfg, broker, log) -> list[str]:
    """Return a list of blocking problems. Empty list means clear to trade."""
    m = mode()
    problems: list[str] = []
    warnings: list[str] = []

    paper_keys = bool(cfg.credentials.paper)

    # ---- 1. mode and credentials must agree -------------------------------
    if m == "live" and paper_keys:
        problems.append(
            "ORB_MODE=live but ALPACA_PAPER=true in .env -- these are paper "
            "keys. Either this directory is not the live instance, or the "
            "wrong .env was copied into it."
        )
    if m == "paper" and not paper_keys:
        problems.append(
            "ORB_MODE=paper but ALPACA_PAPER=false -- LIVE credentials are "
            "loaded in a paper instance. Refusing to trade real money from a "
            "directory whose results you will read as paper."
        )

    # ---- 2. live needs an explicit, human, one-time confirmation ----------
    if m == "live" and not CONFIRM_FILE.exists():
        problems.append(
            f"{CONFIRM_FILE} is missing. Live trading requires it; create it "
            "by hand after reading LIVE-GO.md."
        )

    # ---- 3. the account itself --------------------------------------------
    acct = None
    try:
        acct = broker.trading.get_account()
    except Exception as e:  # noqa: BLE001
        problems.append(f"Could not read the account: {e}")

    equity = 0.0
    if acct is not None:
        status = str(getattr(acct, "status", "")).upper()
        if "ACTIVE" not in status:
            problems.append(f"Account status is {status or '(unknown)'}, not ACTIVE.")
        for flag, label in (
            ("trading_blocked", "trading is blocked"),
            ("account_blocked", "the account is blocked"),
            ("transfers_blocked", "transfers are blocked"),
        ):
            if _is_true(getattr(acct, flag, False)):
                problems.append(f"Alpaca reports {label} ({flag}=True).")
        try:
            equity = float(getattr(acct, "equity", 0) or 0)
        except (TypeError, ValueError):
            equity = 0.0

        live_acct = not _is_true(getattr(acct, "is_paper", not paper_keys))
        if m == "live" and not live_acct and hasattr(acct, "is_paper"):
            problems.append("Alpaca says this is a paper account but ORB_MODE=live.")

    # ---- 4. can the account actually carry the configured size? -----------
    # FINRA retired the pattern-day-trader rule in 2026 and Alpaca replaced it
    # with an intraday margin framework, so a small account may day trade
    # freely -- but a MARGIN account still needs $2,000 of equity to carry an
    # intraday debit, and going under it mid-session is how a live run gets
    # restricted at the worst moment.
    per_pos = float(getattr(cfg.sizing, "max_position_notional", 0) or 0)
    max_trades = int(getattr(cfg.strategy, "max_trades_per_day", 0) or 0)
    if equity > 0 and per_pos > 0:
        concurrent = max(1, min(max_trades or 1, 3))
        worst_notional = per_pos * concurrent
        if m == "live" and worst_notional > equity:
            problems.append(
                f"Sizing can deploy ${worst_notional:,.0f} "
                f"({concurrent} x ${per_pos:,.0f}) against ${equity:,.0f} of "
                "equity. That borrows intraday. Lower "
                "sizing.max_position_notional or strategy.max_trades_per_day "
                "so the total fits inside the account."
            )
        if m == "live" and equity < 2000:
            warnings.append(
                f"Equity ${equity:,.2f} is under the $2,000 margin minimum for "
                "carrying an intraday debit. Stay fully within settled cash."
            )

    # ---- 5. config sanity for live ----------------------------------------
    if m == "live":
        if _is_true(getattr(cfg.strategy, "allow_shorts", False)):
            problems.append(
                "allow_shorts is true. Shorting needs margin and a locate; it "
                "has never been validated here and is off in every swept config."
            )
        loss_cap = float(getattr(cfg.risk, "daily_max_loss_pct", 0) or 0)
        if loss_cap <= 0 or loss_cap > 3.0:
            problems.append(
                f"risk.daily_max_loss_pct is {loss_cap}. Live runs with a "
                "tighter daily breaker than paper -- set it to 3.0 or less."
            )
        if not _is_true(getattr(cfg.risk, "flatten_on_breaker", False)):
            problems.append("risk.flatten_on_breaker is false. Live must flatten.")
        if str(getattr(cfg.runtime, "data_feed", "")).lower() == "iex":
            warnings.append(
                "data_feed is iex: ~4% of the tape. Fills are real but the "
                "bot's view of volume and of bar extremes is partial."
            )

    for w in warnings:
        log.warning("PREFLIGHT WARN: %s", w)
    return problems


def enforce(cfg, broker, log) -> None:
    """Log the mode, run the checks, and raise SystemExit on any problem."""
    m = mode()
    log.info("PREFLIGHT: mode=%s, paper_credentials=%s", m, cfg.credentials.paper)
    problems = check(cfg, broker, log)
    if not problems:
        log.info("PREFLIGHT: all checks passed (%s mode).", m)
        return
    log.critical("PREFLIGHT FAILED -- refusing to trade. %d problem(s):", len(problems))
    for p in problems:
        log.critical("  - %s", p)
    raise SystemExit(2)
