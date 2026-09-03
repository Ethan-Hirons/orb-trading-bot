"""Logging setup: writes to console and a dated file in logs/."""

from __future__ import annotations

import logging
import os
from datetime import date

from .config import ROOT

LOG_DIR = ROOT / "logs"


def get_logger(name: str = "orb_bot") -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    # Offline tools (tests, sweep.py) set ORB_NO_FILE_LOG=1 so they don't
    # write into the live day log that bookkeeping/recaps parse.
    if not os.environ.get("ORB_NO_FILE_LOG"):
        file_path = LOG_DIR / f"orb_{date.today().isoformat()}.log"
        fh = logging.FileHandler(file_path, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger
