"""E20 paper runner: the frozen BTC Donchian ensemble (E19's C5), on its OWN paper account.

See PREREG-BTC-TREND-PAPER-2026-11-11.md. The target weight comes from
survey.donchian_targets, so the rule traded here is the rule that was tested.

    python -u btc_bot.py plan      # read-only: the target weight and the order `run` would send
    python -u btc_bot.py run       # one daily rebalance, a few minutes after 00:00 UTC
    python -u btc_bot.py status    # read-only: account, position, last ledger rows
    python -u btc_bot.py shadow    # read-only: the forward evidence record from daily bars

`plan` and `shadow` need only market data. `run` refuses to place an order unless ALL hold:
  * paper endpoint (hard-coded) and ALPACA_PAPER=true in this checkout's .env;
  * BTC_ACCOUNT in .env equals the account the keys open;
  * the account holds nothing, and has no open orders, in any symbol but BTC/USD;
  * state/BTC_DISARMED does not exist.
Long-only, never above 100% of equity, one market order per day at most. It never imports
ORB or the E16 runner, and it must not share an account with either.
"""

from __future__ import annotations

import csv
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import survey

SYMBOL = "BTC/USD"
POSITION_SYMBOLS = frozenset({"BTC/USD", "BTCUSD"})
HISTORY_FROM = "2021-01-01"      # frozen: every run rebuilds the nine models from this day
BAND = 0.05                      # PREREG: trade only when the weight is off target by more
MIN_NOTIONAL = 10.0              # dollars; smaller differences are not worth an order
FORWARD_FROM = "2026-11-12"      # PREREG: first UTC day that counts as forward evidence
COST = survey.COSTS["btc"][0]    # 30 bp per side, the scored assumption
FILL_WAIT_S = 30
TERMINAL = frozenset({"filled", "canceled", "cancelled", "expired", "rejected"})
LEDGER = Path("state/btc_ledger.csv")
DISARM = Path("state/BTC_DISARMED")
FIELDS = ["run_utc", "signal_day", "close", "target", "equity", "weight_before", "action", "side",
          "notional", "qty", "order_id", "status", "ref_price", "fill_price", "slip_bp",
          "weight_after"]
BARS_URL = "https://data.alpaca.markets/v1beta3/crypto/us/bars"

log = logging.getLogger("btc")


# ---------------------------------------------------------------- the rule (pure functions)

def completed_closes(bars: list[dict], now: datetime) -> pd.Series:
    """UTC daily closes for days that have finished. Alpaca stamps a day's bar at its 00:00 UTC
    start, so the bar stamped today is still forming and is dropped."""
    today = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    rows = {b["t"][:10]: float(b["c"]) for b in bars if b["t"][:10] < today}
    s = pd.Series(rows).sort_index()
    s.index = pd.to_datetime(s.index)
    return s


def check_history(closes: pd.Series, now: datetime) -> str:
    """'' if the series is usable, else why not."""
    if len(closes) < 460:
        return f"only {len(closes)} daily closes; the 360-day model and 90-day volatility need 460"
    yesterday = (now.astimezone(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    if closes.index[-1].strftime("%Y-%m-%d") != yesterday:
        return f"latest completed close is {closes.index[-1].date()}, expected {yesterday}"
    gaps = closes.index.to_series().diff().dt.days.iloc[1:]
    if (gaps.tail(460) != 1).any():
        return "a day is missing in the last 460 closes"
    return ""


def target_weight(closes: pd.Series) -> float:
    return float(survey.donchian_targets(closes).iloc[-1])


def decide(target: float, weight: float, equity: float, price: float, held_qty: float):
    """What to send, as (side, notional, qty) or None. Mirrors survey.apply_threshold:
    trade to the target only when the current weight is more than BAND away from it."""
    if abs(target - weight) <= BAND:
        return None
    delta = (target - weight) * equity
    if abs(delta) < MIN_NOTIONAL:
        return None
    if delta > 0:
        return ("buy", round(delta, 2), None)
    if target <= 0:
        return ("sell", None, held_qty)                    # close the whole position
    return ("sell", None, round(min(held_qty, -delta / price), 8))


# ---------------------------------------------------------------- plumbing

def fetch_daily_bars(session=None, headers: dict | None = None) -> list[dict]:
    import requests
    sess = session or requests.Session()
    if headers:
        sess.headers.update(headers)
    rows, token = [], None
    while True:
        params = {"symbols": SYMBOL, "timeframe": "1Day", "start": HISTORY_FROM, "limit": 10000}
        if token:
            params["page_token"] = token
        r = sess.get(BARS_URL, params=params, timeout=60)
        r.raise_for_status()
        js = r.json()
        rows += js["bars"].get(SYMBOL, [])
        token = js.get("next_page_token")
        if not token:
            return rows


def _env_headers() -> dict:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True))
    return {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
            "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]}


class AlpacaCryptoPaper:
    """The only thing that sends orders. Paper endpoint, BTC/USD, nothing else."""

    def __init__(self):
        from dotenv import load_dotenv
        from alpaca.trading.client import TradingClient
        load_dotenv()
        if os.environ.get("ALPACA_PAPER", "").strip().lower() != "true":
            raise SystemExit("btc_bot trades PAPER only: set ALPACA_PAPER=true in this .env")
        self.trading = TradingClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"],
                                     paper=True)

    def account(self) -> tuple[str, float]:
        a = self.trading.get_account()
        return str(a.account_number), float(a.equity)

    def positions(self) -> dict[str, tuple[float, float]]:
        """{symbol: (quantity, market value)}"""
        return {p.symbol: (float(p.qty), float(p.market_value))
                for p in self.trading.get_all_positions()}

    def open_orders(self) -> list[tuple[str, str]]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest
        return [(str(o.id), o.symbol) for o in
                self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))]

    def market(self, side: str, notional: float | None, qty: float | None) -> str:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
        req = MarketOrderRequest(symbol=SYMBOL, notional=notional, qty=qty,
                                 side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                                 time_in_force=TimeInForce.GTC)
        return str(self.trading.submit_order(order_data=req).id)

    def order(self, oid: str) -> tuple[str, float, float]:
        o = self.trading.get_order_by_id(oid)
        return (getattr(o.status, "value", str(o.status)).lower(),
                float(o.filled_avg_price or 0), float(o.filled_qty or 0))

    def cancel(self, oid: str) -> None:
        self.trading.cancel_order_by_id(oid)

    def bars(self) -> list[dict]:
        return fetch_daily_bars(headers=_env_headers())

    def last_price(self) -> float:
        import requests
        r = requests.get("https://data.alpaca.markets/v1beta3/crypto/us/latest/trades",
                         params={"symbols": SYMBOL}, headers=_env_headers(), timeout=30)
        r.raise_for_status()
        return float(r.json()["trades"][SYMBOL]["p"])


def notify(title: str, body: str, priority: str = "default") -> None:
    script = Path("deploy/notify.sh")
    try:
        if script.exists():
            subprocess.run([str(script), f"E20 BTC {title}", body, priority], check=False)
    except OSError as e:
        log.warning("notify failed: %s", e)


def _ledger(row: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    new = not LEDGER.exists()
    with LEDGER.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def already_ran(signal_day: str) -> bool:
    if not LEDGER.exists():
        return False
    with LEDGER.open() as fh:
        return any(r["signal_day"] == signal_day and r["action"] != "refused"
                   for r in csv.DictReader(fh))


# ---------------------------------------------------------------- one daily run

def refuse_reason(broker, expected_account: str) -> str:
    """'' if it is safe to trade, else why not. Checked before anything is sent."""
    if DISARM.exists():
        return f"{DISARM} exists"
    acct, _ = broker.account()
    if not expected_account or acct != expected_account:
        return f"account {acct} is not BTC_ACCOUNT ({expected_account or 'unset'})"
    foreign = sorted(s for s in broker.positions() if s not in POSITION_SYMBOLS)
    if foreign:
        return f"account holds other symbols: {foreign}"
    foreign = sorted({s for _, s in broker.open_orders() if s not in POSITION_SYMBOLS})
    if foreign:
        return f"account has open orders in other symbols: {foreign}"
    return ""


def run_once(broker, now: datetime, expected_account: str, sleep=time.sleep) -> dict:
    """One rebalance. Returns the ledger row (also written). Never raises on a refusal."""
    row = {"run_utc": now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}
    why = refuse_reason(broker, expected_account)
    if why:
        row.update(action="refused", status=why)
        _ledger(row)
        notify("REFUSED", why, "urgent")
        return row
    closes = completed_closes(broker.bars(), now)
    bad = check_history(closes, now)
    if bad:
        row.update(action="refused", status=bad)
        _ledger(row)
        notify("REFUSED", bad, "high")
        return row
    signal_day = closes.index[-1].strftime("%Y-%m-%d")
    row.update(signal_day=signal_day, close=round(float(closes.iloc[-1]), 2))
    if already_ran(signal_day):
        row.update(action="skipped", status="already ran for this signal day")
        return row
    for oid, _ in broker.open_orders():          # a stale BTC order from an earlier run
        log.warning("cancelling leftover order %s", oid)
        broker.cancel(oid)
    target = target_weight(closes)
    _, equity = broker.account()
    held_qty, held_value = next((v for s, v in broker.positions().items()
                                 if s in POSITION_SYMBOLS), (0.0, 0.0))
    weight = held_value / equity if equity > 0 else 0.0
    price = broker.last_price()
    row.update(target=round(target, 4), equity=round(equity, 2), weight_before=round(weight, 4),
               ref_price=price)
    order = decide(target, weight, equity, price, held_qty)
    if order is None:
        row.update(action="hold", weight_after=round(weight, 4))
        _ledger(row)
        return row
    side, notional, qty = order
    oid = broker.market(side, notional, qty)
    status, fill, filled = "new", 0.0, 0.0
    for _ in range(FILL_WAIT_S):
        status, fill, filled = broker.order(oid)
        if status in TERMINAL:
            break
        sleep(1)
    if status not in TERMINAL:
        broker.cancel(oid)
        sleep(2)
        status, fill, filled = broker.order(oid)
    slip = (fill / price - 1) * 1e4 * (1 if side == "buy" else -1) if fill else ""
    _, equity2 = broker.account()
    held2 = next((v for s, v in broker.positions().items() if s in POSITION_SYMBOLS), (0.0, 0.0))
    row.update(action="trade", side=side, notional=notional or "", qty=qty or "", order_id=oid,
               status=status, fill_price=fill or "", slip_bp=round(slip, 2) if slip != "" else "",
               weight_after=round(held2[1] / equity2, 4) if equity2 > 0 else "")
    _ledger(row)
    if status != "filled":
        notify("ORDER NOT FILLED", f"{side} {notional or qty}: {status}, filled {filled}", "urgent")
    return row


# ---------------------------------------------------------------- commands

def cmd_plan() -> None:
    now = datetime.now(timezone.utc)
    closes = completed_closes(fetch_daily_bars(headers=_env_headers()), now)
    bad = check_history(closes, now)
    tg = survey.donchian_targets(closes)
    print(f"signal day {closes.index[-1].date()}  close {closes.iloc[-1]:,.2f}  "
          f"{len(closes)} closes since {closes.index[0].date()}" + (f"  WARNING: {bad}" if bad else ""))
    print("target weight, last 10 days: " +
          "  ".join(f"{d.strftime('%m-%d')} {v:.3f}" for d, v in tg.tail(10).items()))
    print(f"today's target: {tg.iloc[-1]:.1%} of equity in BTC. From flat on a $10,000 account, "
          f"`run` would send: {decide(float(tg.iloc[-1]), 0.0, 10_000, float(closes.iloc[-1]), 0.0)}")


def cmd_shadow() -> None:
    """The evidence: the frozen rule on Alpaca's daily closes from FORWARD_FROM, 30 bp a side."""
    import numpy as np
    now = datetime.now(timezone.utc)
    closes = completed_closes(fetch_daily_bars(headers=_env_headers()), now)
    px = pd.DataFrame({"c": closes, "o": closes.shift(1)})
    w, turn = survey.apply_threshold(survey.donchian_targets(closes),
                                     closes.pct_change().shift(-1), BAND, False)
    sim = survey.simulate(w, turn, px, pd.Series(0.0, index=closes.index), COST)
    fwd = sim[sim.index >= FORWARD_FROM]
    n = len(fwd)
    print(f"E20 shadow: {n} forward days from {FORWARD_FROM} (latest close {closes.index[-1].date()})")
    if n < 30:
        print("  fewer than 30 days: nothing to report yet")
        return
    bench = closes.pct_change().reindex(fwd.index).fillna(0.0)
    a, b, t = survey.nw_alpha(fwd["raw"].to_numpy(), bench.to_numpy())
    print(f"  strategy total {(1 + fwd['raw']).prod() - 1:+.2%}, BTC buy and hold "
          f"{(1 + bench).prod() - 1:+.2%}, mean weight {fwd['w'].mean():.2f}")
    print(f"  alpha {a * 365:+.2%}/yr, beta {b:.2f}, alpha t {t:+.2f}  "
          f"(looks at 365 and 730 days; see the PREREG)")


def cmd_status() -> None:
    b = AlpacaCryptoPaper()
    acct, equity = b.account()
    print(f"account {acct}  equity {equity:,.2f}  positions {b.positions()}  "
          f"open orders {b.open_orders()}  disarmed {DISARM.exists()}")
    if LEDGER.exists():
        print("".join(LEDGER.read_text().splitlines(keepends=True)[-6:]))


def cmd_run() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    broker = AlpacaCryptoPaper()
    row = run_once(broker, datetime.now(timezone.utc), os.environ.get("BTC_ACCOUNT", "").strip())
    log.info("%s", {k: v for k, v in row.items() if v != ""})
    return 2 if row.get("action") == "refused" else 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "plan"
    sys.exit({"plan": cmd_plan, "run": cmd_run, "status": cmd_status, "shadow": cmd_shadow}[cmd]() or 0)
