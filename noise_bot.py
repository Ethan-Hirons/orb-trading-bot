"""E16 paper runner: the frozen QQQ noise-area rule, on its OWN paper account.

See PREREG-QQQ-NOISE-PAPER-2026-10-27.md. Every decision comes from index_scan.py
(sigma_at, bands, target), so the rule traded here is the rule that was tested.

    python -u noise_bot.py run         # one session: plan 09:47, trade 10:00-15:30, exit 15:58
    python -u noise_bot.py status      # account, position, recent ledger rows (read-only)
    python -u noise_bot.py reconcile   # Stage A numbers vs the SIP shadow (index_scan.py fetch first)

Refuses to place a single order unless ALL of these hold:
  * paper endpoint (hard-coded) and ALPACA_PAPER=true in this checkout's .env;
  * NOISE_ACCOUNT in .env equals the account the keys open (setup-noise.sh writes it);
  * the account holds nothing, and has no open orders, in any symbol but QQQ.
Never holds overnight, never stacks orders: market orders close the position from 15:58,
each one filled or cancelled-and-confirmed before the next is sent; verified flat at 16:05.
Anything still held then is alerted and left for the next morning, never queued after hours.
`touch state/NOISE_DISARMED` skips sessions.

This is not the ORB bot and never imports it: ORB's flatten-all would close these trades.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import subprocess
import sys
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import index_scan as ix
from index_scan import CHECKPOINTS, ET, LOOKBACK

SYMBOL = "QQQ"
NOTIONAL = 10_000          # dollars per position, fixed (PREREG build list)
PLAN_AT = dtime(9, 47)     # SIP's 09:30 bar is past the free feed's 15-minute delay
# Close-out (2026-11-01 fix). Market-on-close is gone: on 2026-10-30 an MOC for 14 filled 9
# in the auction and expired the rest at 16:02, a partial fill nothing could see or repair
# before the close. Regular-hours market orders from 15:58 can be watched and retried.
EXIT_AT = dtime(15, 58)
EXIT_DEADLINE = dtime(15, 59, 40)  # no new closing order after this: it might not fill by 16:00
VERIFY_AT = dtime(16, 5)
FILL_WAIT_S = 20           # one closing attempt; then cancel it and confirm before retrying
CANCEL_WAIT_S = 10
TERMINAL = frozenset({"filled", "canceled", "cancelled", "expired", "rejected", "done_for_day"})
LEDGER_V = 2               # close rows written by the fixed close-out; Stage A counts from here
STALE_S = 120              # an IEX trade older than this is not a price
HISTORY_DAYS = 45          # calendar days of SIP bars: comfortably > 14 full sessions
LEDGER = Path("state/noise_ledger.csv")
DISARM = Path("state/NOISE_DISARMED")
FIELDS = ["day", "checkpoint", "price", "price_time", "sigma", "ub", "lb",
          "pos_before", "pos_after", "orders"]

log = logging.getLogger("noise")


# ---------------------------------------------------------------- plumbing

class Clock:
    def now(self) -> datetime:
        return datetime.now(ET)

    def sleep_until(self, t: datetime) -> None:
        while (left := (t - self.now()).total_seconds()) > 0:
            time.sleep(min(left, 30))


class AlpacaPaper:
    """The only thing that talks to Alpaca. Paper endpoint, QQQ, nothing else."""

    def __init__(self):
        from dotenv import load_dotenv
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient

        load_dotenv()
        if os.environ.get("ALPACA_PAPER", "").strip().lower() != "true":
            raise SystemExit("noise_bot trades PAPER only: set ALPACA_PAPER=true in this .env")
        key, sec = os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]
        self.trading = TradingClient(key, sec, paper=True)
        self.data = StockHistoricalDataClient(key, sec)

    def account_number(self) -> str:
        return str(self.trading.get_account().account_number)

    def positions(self) -> dict[str, float]:
        """Signed quantities: short positions are negative."""
        out = {}
        for p in self.trading.get_all_positions():
            side = getattr(p.side, "value", str(p.side)).lower()
            out[p.symbol] = -abs(float(p.qty)) if "short" in side else abs(float(p.qty))
        return out

    def _open_orders(self):
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest
        return self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))

    def open_order_symbols(self) -> set[str]:
        return {o.symbol for o in self._open_orders()}

    def open_order_ids(self) -> list[str]:
        """Working QQQ orders, queued pre-open or after-hours ones included."""
        return [str(o.id) for o in self._open_orders() if o.symbol == SYMBOL]

    def market(self, side: str, qty: int) -> str:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
        req = MarketOrderRequest(symbol=SYMBOL, qty=qty,
                                 side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                                 time_in_force=TimeInForce.DAY)
        return str(self.trading.submit_order(order_data=req).id)

    def order(self, oid: str) -> tuple[str, float]:
        o = self.trading.get_order_by_id(oid)
        return getattr(o.status, "value", str(o.status)).lower(), float(o.filled_avg_price or 0)

    def filled(self, oid: str) -> float:
        return float(self.trading.get_order_by_id(oid).filled_qty or 0)

    def cancel(self, oid: str) -> None:
        self.trading.cancel_order_by_id(oid)

    def latest_trade(self) -> tuple[float, datetime]:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockLatestTradeRequest
        t = self.data.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=SYMBOL, feed=DataFeed.IEX))[SYMBOL]
        return float(t.price), t.timestamp.astimezone(ET)

    def minute_bars(self, start: datetime, end: datetime) -> dict[str, list]:
        """SIP, split- and dividend-adjusted, regular hours, in index_scan's tuple layout."""
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        req = StockBarsRequest(symbol_or_symbols=SYMBOL, timeframe=TimeFrame(1, TimeFrameUnit.Minute),
                               start=start, end=end, feed=DataFeed.SIP, adjustment=Adjustment.ALL)
        days: dict[str, list] = {}
        for b in self.data.get_stock_bars(req).data.get(SYMBOL, []):
            t = b.timestamp.astimezone(ET)
            minute = t.hour * 60 + t.minute - 570
            if 0 <= minute < 390:
                days.setdefault(t.strftime("%Y-%m-%d"), []).append(
                    (minute, float(b.open), float(b.high), float(b.low), float(b.close),
                     float(b.volume), float(getattr(b, "vwap", 0) or 0)))
        return {d: sorted(v) for d, v in sorted(days.items())}

    def calendar(self, start: date, end: date) -> dict[str, str]:
        """{day: close 'HH:MM'} for every session in the range."""
        from alpaca.trading.requests import GetCalendarRequest
        return {str(c.date): ix._hhmm(c.close)
                for c in self.trading.get_calendar(GetCalendarRequest(start=start, end=end))}


def notify(title: str, body: str, priority: str = "default") -> None:
    script = Path("deploy/notify.sh")
    try:
        if script.exists():
            subprocess.run([str(script), f"E16 QQQ {title}", body, priority], check=False)
    except OSError as e:                  # e.g. no bash on a dev laptop: never fatal
        log.warning("notify failed: %s", e)


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _at(day: date, t: dtime) -> datetime:
    return datetime.combine(day, t, tzinfo=ET)


def _ledger(row: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    new = not LEDGER.exists()
    with LEDGER.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


# ---------------------------------------------------------------- the plan

def plan(history: dict[str, list], cal: dict[str, str], today: str):
    """Today's (sigma, UB, LB) per checkpoint, exactly as index_scan.h3_rows builds them.

    Returns (levels, "") or (None, reason). The rule's own skips apply: fewer than 14 full
    sessions, a previous session with a feed gap, or no 09:30 bar.
    """
    early = frozenset(d for d, close in cal.items() if close < "16:00")
    ss = ix.sessions({d: b for d, b in history.items() if d < today}, early)
    hist = []
    for s in ss:
        if s.full:
            hist = (hist + [s])[-LOOKBACK:]
    if len(hist) < LOOKBACK:
        return None, f"only {len(hist)} full sessions of history"
    prev = ss[-1]
    if prev.broken:
        return None, f"previous session {prev.day} has a feed gap"
    bars = history.get(today, [])
    if not bars or bars[0][0] > 2:
        return None, "no SIP 09:30 bar for today"
    open_ = bars[0][1]
    levels = {}
    for m in CHECKPOINTS:
        sigma = ix.sigma_at(hist, m)
        levels[m] = (sigma, *ix.bands(open_, prev.close, sigma))
    return {"open": open_, "prev_close": prev.close, "prev_day": prev.day, "levels": levels}, ""


def orders_for(pos: int, new: int, held: float, price: float) -> list[tuple[str, int]]:
    """Close what is held, then open the new side at NOTIONAL. Alpaca won't flip in one order."""
    out = []
    if pos:
        out.append(("sell" if pos > 0 else "buy", int(abs(held))))
    if new:
        out.append(("buy" if new > 0 else "sell", int(NOTIONAL // price)))
    return out


def _status(broker, oid: str) -> tuple[str, float]:
    try:
        return broker.order(oid)
    except Exception as e:  # noqa: BLE001
        log.warning("order %s lookup failed: %s", oid, e)
        return "unknown", 0.0


def wait_fill(broker, clock, oid: str, timeout_s: float = 30) -> float | None:
    """Fill price, or None if the order ended unfilled or is still working at the deadline."""
    deadline = clock.now() + timedelta(seconds=timeout_s)
    while True:
        status, price = _status(broker, oid)
        if status == "filled" and price > 0:
            return price
        if status in TERMINAL or clock.now() >= deadline:
            return None
        clock.sleep_until(clock.now() + timedelta(seconds=1))


def settle_cancel(broker, clock, oid: str) -> str:
    """Cancel and wait until the order is dead (or filled). Returns its final status.

    A status outside TERMINAL means the cancel wasn't confirmed: the caller must not send
    another order for the same shares, or both could fill.
    """
    try:
        broker.cancel(oid)
    except Exception as e:  # noqa: BLE001  (it may have filled a moment ago)
        log.warning("cancel %s failed: %s", oid, e)
    deadline = clock.now() + timedelta(seconds=CANCEL_WAIT_S)
    while True:
        status = _status(broker, oid)[0]
        if status in TERMINAL or clock.now() >= deadline:
            return status
        clock.sleep_until(clock.now() + timedelta(seconds=1))


def cancel_open(broker, clock) -> list[list[str]]:
    """Cancel every working QQQ order and confirm each; returns [[order id, final status]]."""
    return [[oid, settle_cancel(broker, clock, oid)] for oid in broker.open_order_ids()]


# ---------------------------------------------------------------- one session

def run(broker=None, clock=None, expected_account: str | None = None) -> int:
    broker = broker or AlpacaPaper()
    clock = clock or Clock()
    expected_account = expected_account or os.environ.get("NOISE_ACCOUNT", "").strip()
    now = clock.now()
    today = now.date()
    day = today.isoformat()

    if DISARM.exists():
        log.info("%s present: skipping today's session", DISARM)
        return 0
    cal = broker.calendar(today - timedelta(days=HISTORY_DAYS), today)
    if day not in cal:
        log.info("no session today (%s)", day)
        return 0
    if cal[day] < "16:00":
        log.info("half-day (close %s): not traded, per the PREREG", cal[day])
        return 0

    acct = broker.account_number()
    if not expected_account or acct != expected_account:
        msg = f"keys open account {acct}, NOISE_ACCOUNT is {expected_account or 'unset'}: refusing"
        log.error(msg)
        notify("REFUSED", msg, "urgent")
        return 2
    held = broker.positions()
    foreign = (set(held) | broker.open_order_symbols()) - {SYMBOL}
    if foreign:
        msg = f"account {acct} holds/orders {sorted(foreign)}: not the E16 account? refusing"
        log.error(msg)
        notify("REFUSED", msg, "urgent")
        return 2

    open_dt = _at(today, dtime(9, 30))
    # A working order at startup is never this session's: an after-hours close queued by an
    # older 16:05 path, or one left by a crash. Kill it before acting, or it fills on top.
    for oid, status in cancel_open(broker, clock):
        log.warning("cancelled leftover order %s at startup (%s)", oid, status)
        notify("leftover order", f"cancelled working order {oid} at startup ({status})", "high")
    held = broker.positions()
    pos = _sign(held.get(SYMBOL, 0.0))
    if pos and now < open_dt:
        # an overnight leftover: E16 never holds overnight, so close it at the open
        side, qty = orders_for(pos, 0, held[SYMBOL], 1.0)[0]
        log.warning("leftover %s %s from a previous session: closing at the open", held[SYMBOL], SYMBOL)
        notify("leftover position", f"{held[SYMBOL]} {SYMBOL} carried overnight; closing at the open", "high")
        oid = broker.market(side, qty)
        clock.sleep_until(open_dt + timedelta(minutes=1))
        if wait_fill(broker, clock, oid) is None:
            settle_cancel(broker, clock, oid)
        pos = _sign(broker.positions().get(SYMBOL, 0.0))
        if pos:
            msg = f"leftover still held after the open ({broker.positions()}): not trading today"
            log.error(msg)
            notify("REFUSED", msg, "urgent")
            return 2
    log.info("E16 session %s on account %s, starting position %+d", day, acct, pos)

    clock.sleep_until(_at(today, PLAN_AT))
    try:
        history = broker.minute_bars(_at(today - timedelta(days=HISTORY_DAYS), dtime(0, 0)),
                                     clock.now() - timedelta(minutes=16))
        p, why = plan(history, cal, day)
    except Exception as e:  # noqa: BLE001
        p, why = None, f"history fetch failed: {e}"
    if p is None:
        log.error("no trading today: %s", why)
        notify("no session", why, "high")
        return _close_out(broker, clock, today, pos)
    log.info("open %.4f, prev close %.4f (%s)", p["open"], p["prev_close"], p["prev_day"])
    for m, (sg, ub, lb) in p["levels"].items():
        log.info("  %s  sigma %.4f%%  UB %.4f  LB %.4f", (open_dt + timedelta(minutes=m)).strftime("%H:%M"),
                 sg * 100, ub, lb)

    for m in CHECKPOINTS:
        t = open_dt + timedelta(minutes=m)
        if clock.now() > t + timedelta(minutes=5):
            continue                      # restarted after this checkpoint had passed
        clock.sleep_until(t + timedelta(seconds=2))
        sg, ub, lb = p["levels"][m]
        row = {"day": day, "checkpoint": t.strftime("%H:%M"), "sigma": f"{sg:.6f}",
               "ub": f"{ub:.4f}", "lb": f"{lb:.4f}", "pos_before": pos}
        try:
            price, ts = broker.latest_trade()
        except Exception as e:  # noqa: BLE001
            price, ts = None, None
            log.warning("%s: no price (%s), holding", row["checkpoint"], e)
        if price is None or (clock.now() - ts).total_seconds() > STALE_S:
            row.update(pos_after=pos, orders="no fresh price: held")
            _ledger(row)
            continue
        new = ix.target(pos, price, ub, lb)
        done = []
        if new != pos:
            for side, qty in orders_for(pos, new, broker.positions().get(SYMBOL, 0.0), price):
                oid = broker.market(side, qty)
                fill = wait_fill(broker, clock, oid)
                done.append([side, qty, fill, oid])
                if fill is None:
                    status = settle_cancel(broker, clock, oid)   # never leave it working
                    log.error("%s %s %s did not fill (%s)", side, qty, SYMBOL, status)
                    notify("order not filled", f"{row['checkpoint']} {side} {qty}: {status}", "high")
                    break
            pos = _sign(broker.positions().get(SYMBOL, 0.0))   # the broker is the truth
        row.update(price=f"{price:.4f}", price_time=ts.strftime("%H:%M:%S"), pos_after=pos,
                   orders=json.dumps(done))
        _ledger(row)
        log.info("%s  %.4f  [%.4f, %.4f]  %+d -> %+d  %s", row["checkpoint"], price, lb, ub,
                 row["pos_before"], pos, done or "")
    return _close_out(broker, clock, today, pos)


def _close_out(broker, clock, today: date, pos: int) -> int:
    """Close with market orders from 15:58; verify flat at 16:05. Never holds overnight.

    One closing order works at a time: each attempt fills, or is cancelled and confirmed
    dead, before the next is sent, so a stalled order and its retry can never both fill.
    If the account is still not flat at 16:05, nothing is queued after hours (an after-hours
    close would fill at the next open on top of the morning's own close). Working orders are
    cancelled, the alert is urgent, and the next session's startup closes the leftover.
    """
    day = today.isoformat()
    clock.sleep_until(_at(today, EXIT_AT))
    stray = cancel_open(broker, clock)            # nothing may fill on top of the exit
    exits = []
    while clock.now() < _at(today, EXIT_DEADLINE):
        held = broker.positions().get(SYMBOL, 0.0)
        if not held:
            break
        side, qty = orders_for(_sign(held), 0, held, 1.0)[0]
        at = clock.now()
        oid = broker.market(side, qty)
        status = "filled" if wait_fill(broker, clock, oid, FILL_WAIT_S) else settle_cancel(broker, clock, oid)
        price = _status(broker, oid)[1]
        exits.append({"side": side, "qty": qty, "at": at.strftime("%H:%M:%S"), "status": status,
                      "price": price, "filled": broker.filled(oid), "oid": oid})
        log.info("close %s %s at %s: %s %s", side, qty, exits[-1]["at"], status, price or "")
        if status not in TERMINAL:
            log.error("closing order %s still %s after cancel: no further attempts", oid, status)
            break
    clock.sleep_until(_at(today, VERIFY_AT))
    held = broker.positions().get(SYMBOL, 0.0)
    stray += cancel_open(broker, clock)
    flat = not held
    if not flat:
        msg = (f"{held:+g} {SYMBOL} still held at 16:05. Nothing queued after hours; "
               "the next session's startup closes it at the open. Check the account.")
        log.error("NOT FLAT: %s", msg)
        notify("NOT FLAT", msg, "urgent")
    filled = [e["price"] for e in exits if e["price"]]
    _ledger({"day": day, "checkpoint": "close", "pos_before": pos, "pos_after": 0 if flat else "?",
             "orders": json.dumps({"v": LEDGER_V, "exits": exits, "cancelled": stray}),
             "price": f"{filled[0]:.4f}" if filled else ""})
    trades = sum(1 for r in _rows(day) if r["orders"].startswith("[["))
    notify("session done", f"{day}: {trades} decision(s) traded, flat={flat}")
    log.info("session %s done: flat=%s", day, flat)
    return 0 if flat else 3


def _rows(day: str | None = None) -> list[dict]:
    if not LEDGER.exists():
        return []
    with LEDGER.open() as fh:
        return [r for r in csv.DictReader(fh) if day is None or r["day"] == day]


# ---------------------------------------------------------------- read-only

def status() -> int:
    b = AlpacaPaper()
    a = b.trading.get_account()
    print(f"account {a.account_number}  equity ${float(a.equity):,.2f}  "
          f"NOISE_ACCOUNT {os.environ.get('NOISE_ACCOUNT', 'unset')}")
    print(f"positions {b.positions() or 'none'}")
    for r in _rows()[-15:]:
        print(f"  {r['day']} {r['checkpoint']:>5}  {r['price']:>9}  {r['pos_before']:>2} -> "
              f"{r['pos_after']:>2}  {r['orders']}")
    return 0


def shadow_path(ss: list, day: str):
    """SIP replay of one session: [(m, P(m), UB, LB, position after)], h3_rows's skips applied."""
    hist = []
    for i, s in enumerate(ss):
        if s.day == day:
            if not (i and s.full and not ss[i - 1].broken and len(hist) == LOOKBACK):
                return None
            pos, out = 0, []
            for m in CHECKPOINTS:
                ub, lb = ix.bands(s.open, ss[i - 1].close, ix.sigma_at(hist, m))
                pos = ix.target(pos, s.p(m), ub, lb)
                out.append((m, s.p(m), ub, lb, pos))
            return out, s
        if s.full:
            hist = (hist + [s])[-LOOKBACK:]
    return None


def _close_record(row: dict | None):
    """A fixed-code close row's {"v", "exits", "cancelled"}, or None for older rows."""
    try:
        rec = json.loads((row or {}).get("orders") or "null")
    except ValueError:
        return None
    return rec if isinstance(rec, dict) and rec.get("v", 0) >= LEDGER_V else None


def _sip_at(s, hhmmss: str) -> float:
    """SIP price at the minute an exit was sent (same-minute reference, as for entries)."""
    hh, mm = int(hhmmss[:2]), int(hhmmss[3:5])
    m = hh * 60 + mm - 570
    return s.p(m) if 1 <= m <= 389 else s.close


def reconcile() -> int:
    """Stage A: coverage, decision agreement with the SIP shadow, and slippage per side.

    Stage A restarts with the fixed close-out (PREREG change log, 2026-11-01): it counts
    from the first session whose close row the fixed code wrote, and every later session.
    """
    rows = _rows()
    if not rows:
        print("ledger is empty")
        return 0
    all_days = sorted({r["day"] for r in rows})
    fixed = [d for d in all_days if _close_record(next(
        (r for r in _rows(d) if r["checkpoint"] == "close"), None))]
    if not fixed:
        print(f"E16 Stage A: restarts with the first fixed-code session; none yet "
              f"({len(all_days)} pre-fix session(s) ignored)")
        return 0
    days = [d for d in all_days if d >= fixed[0]]
    ss = ix.sessions(ix.load((SYMBOL,))[SYMBOL])
    agree = total = covered = flat_days = 0
    slips, timing = [], []
    for day in days:
        mine = {r["checkpoint"]: r for r in _rows(day)}
        covered += sum(1 for r in mine.values() if r["checkpoint"] != "close"
                       and r["price"]) == len(CHECKPOINTS)
        flat_days += mine.get("close", {}).get("pos_after") == "0"
        sp = shadow_path(ss, day)
        if sp is None:
            print(f"  {day}: no SIP shadow (not fetched yet, or a rule skip)")
            continue
        path, s = sp
        for m, ref, ub, lb, spos in path:
            hhmm = f"{(570 + m) // 60:02d}:{(570 + m) % 60:02d}"
            r = mine.get(hhmm)
            if not r or not r["price"]:
                continue
            total += 1
            agree += int(r["pos_after"]) == spos
            for side, qty, fill, _ in json.loads(r["orders"] or "[]"):
                if fill:
                    slips.append((fill / ref - 1) if side == "buy" else (1 - fill / ref))
        rec = _close_record(mine.get("close")) or {"exits": []}
        for e in rec["exits"]:
            if e["filled"] and e["price"]:
                ref, sgn = _sip_at(s, e["at"]), (1 if e["side"] == "buy" else -1)
                slips.append(sgn * (e["price"] / ref - 1))      # cost: same-minute SIP price
                timing.append(sgn * (e["price"] / s.close - 1))  # vs the shadow's close: not a cost
    n = len(days)
    mean_slip = sum(slips) / len(slips) * 1e4 if slips else float("nan")
    print(f"E16 Stage A (from {days[0]}, {len(all_days) - n} pre-fix session(s) ignored): "
          f"{n} sessions, full coverage {covered}/{n}, verified flat {flat_days}/{n}")
    print(f"  decision agreement with SIP shadow: {agree}/{total} "
          f"({agree / total:.1%})" if total else "  no comparable checkpoints yet")
    print(f"  adverse slippage per side: mean {mean_slip:+.2f} bp over {len(slips)} fills "
          f"(gate <= 2 bp; c* = max(0.5, this))")
    if timing:
        print(f"  exit timing vs the shadow's close (not a cost): mean "
              f"{sum(timing) / len(timing) * 1e4:+.2f} bp over {len(timing)} exits")
    if n >= 20:
        ok = covered >= 19 and flat_days == n and total and agree / total >= 0.95 and mean_slip <= 2
        print(f"  STAGE A {'PASS' if ok else 'FAIL'} (criterion 4, isolation, is checked by hand)")
    return 0


def _setup_logging() -> None:
    Path("logs").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    for h in (logging.StreamHandler(sys.stdout),
              logging.FileHandler(f"logs/noise_{datetime.now(ET):%Y-%m-%d}.log")):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "run":
        _setup_logging()
    sys.exit({"run": run, "status": status, "reconcile": reconcile}[cmd]())
