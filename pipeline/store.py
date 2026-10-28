"""Unified on-disk cache for research data.

Replaces three ad-hoc caches that do not share anything:
    .sweep_cache    pickled screener Candidates
    .replay_cache   minute bars (replay.py)
    .qqq_cache      Track B bars

Why one store: the sample-size bottleneck. 150 real trades is not enough to
validate anything (see ANALYSIS-2026-10-25). The way out is simulating years
of sessions, and that is only practical if every tool reads the same cache
instead of re-fetching the same bars three times.

Deliberately dependency-light and source-agnostic: the store never imports
alpaca. Callers pass a `fetch` callable, so the whole thing is testable
offline with synthetic data.

Layout:
    .pipeline_cache/<kind>/<feed>/<symbol>/<key>.json.gz

Values are JSON, gzipped. Inspectable with `zcat`, no pickle, no version
coupling to pandas or the SDK.
"""

from __future__ import annotations

import gzip
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from . import CACHE


def _safe(part: str) -> str:
    """Filesystem-safe path component."""
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(part))


class Store:
    """Read-through cache. Call get() with a fetcher; it only hits the network
    on a miss."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else CACHE
        self.hits = 0
        self.misses = 0
        self.fetched = 0
        self.errors = 0

    # ----- paths -----

    def path(self, kind: str, symbol: str, key: str, feed: str = "na") -> Path:
        return (self.root / _safe(kind) / _safe(feed) / _safe(symbol)
                / f"{_safe(key)}.json.gz")

    # ----- primitives -----

    def has(self, kind: str, symbol: str, key: str, feed: str = "na") -> bool:
        return self.path(kind, symbol, key, feed).exists()

    def read(self, kind: str, symbol: str, key: str,
             feed: str = "na") -> Any | None:
        p = self.path(kind, symbol, key, feed)
        if not p.exists():
            return None
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                return json.load(f)["data"]
        except Exception:
            # A corrupt entry is a cache miss, never a crash. Do not delete:
            # rm is blocked on some mounts. It will simply be overwritten.
            return None

    def write(self, kind: str, symbol: str, key: str, data: Any,
              feed: str = "na") -> None:
        p = self.path(kind, symbol, key, feed)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {"kind": kind, "symbol": symbol, "key": key, "feed": feed,
                   "written": time.time(), "data": data}
        tmp = p.with_suffix(".tmp")
        try:
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                json.dump(payload, f, separators=(",", ":"))
            try:
                os.replace(tmp, p)
            except OSError:
                # Some mounts refuse unlink; fall back to a direct write.
                with gzip.open(p, "wt", encoding="utf-8") as f:
                    json.dump(payload, f, separators=(",", ":"))
        except Exception:
            self.errors += 1

    # ----- read-through -----

    def get(self, kind: str, symbol: str, key: str,
            fetch: Callable[[], Any] | None = None,
            feed: str = "na", allow_empty: bool = False) -> Any | None:
        """Return cached value, else call `fetch` once and cache the result.

        An empty result is NOT cached unless allow_empty, so a transient API
        failure that returns [] does not poison the cache for that symbol-day.
        """
        cached = self.read(kind, symbol, key, feed)
        if cached is not None:
            self.hits += 1
            return cached
        self.misses += 1
        if fetch is None:
            return None
        try:
            data = fetch()
        except Exception:
            self.errors += 1
            return None
        self.fetched += 1
        if data or allow_empty:
            self.write(kind, symbol, key, data, feed)
        return data

    # ----- inventory -----

    def inventory(self) -> dict:
        """What is actually in the store. Used by the dashboard and by
        prefetch to report coverage without re-walking the network."""
        out: dict[str, Any] = {"root": str(self.root), "entries": 0,
                               "bytes": 0, "by_kind": {}, "symbols": set()}
        if not self.root.exists():
            out["symbols"] = []
            return out
        by_kind: dict[str, dict] = defaultdict(
            lambda: {"entries": 0, "bytes": 0, "symbols": set()})
        for p in self.root.rglob("*.json.gz"):
            try:
                rel = p.relative_to(self.root).parts
            except ValueError:
                continue
            if len(rel) < 4:
                continue
            kind, _feed, symbol = rel[0], rel[1], rel[2]
            size = p.stat().st_size
            out["entries"] += 1
            out["bytes"] += size
            by_kind[kind]["entries"] += 1
            by_kind[kind]["bytes"] += size
            by_kind[kind]["symbols"].add(symbol)
            out["symbols"].add(symbol)
        out["by_kind"] = {k: {"entries": v["entries"], "bytes": v["bytes"],
                              "symbols": len(v["symbols"])}
                          for k, v in by_kind.items()}
        out["symbols"] = sorted(out["symbols"])
        return out

    def stats(self) -> str:
        return (f"cache hits {self.hits}, misses {self.misses}, "
                f"fetched {self.fetched}, errors {self.errors}")


def missing(store: Store, kind: str, pairs: Iterable[tuple[str, str]],
            feed: str = "na") -> list[tuple[str, str]]:
    """Which (symbol, key) pairs are not cached yet. Lets prefetch report the
    work before doing it."""
    return [(s, k) for s, k in pairs if not store.has(kind, s, k, feed)]
