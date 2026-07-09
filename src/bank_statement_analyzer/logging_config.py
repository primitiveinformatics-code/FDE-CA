"""App-wide logging setup.

Writes a rotating logfile under the OS-appropriate app-data directory so
a user can attach it to a support request. Only ever logs run
progress/metadata (file names, stage names, token counts, exceptions) —
never raw transaction narrations or account data, which are masked
before they leave `pii.masker` in the first place.
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

APP_DIR_NAME = "BankStatementAnalyzer"
LOG_FILE_NAME = "bank_statement_analyzer.log"

_configured = False


def log_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    path = os.path.join(base, APP_DIR_NAME, "logs")
    os.makedirs(path, exist_ok=True)
    return path


def log_file_path() -> str:
    return os.path.join(log_dir(), LOG_FILE_NAME)


def _level_from_env(default: int) -> int:
    """`BSA_LOG_LEVEL` env var override (e.g. DEBUG) for verbose tracing
    without a code change. Falls back to `default` if unset/invalid."""
    name = os.environ.get("BSA_LOG_LEVEL", "").strip().upper()
    return logging.getLevelName(name) if name in logging._nameToLevel else default


def configure_logging(level: int | None = None) -> str:
    """Attach a rotating file handler (and a console handler) to the root
    logger. Idempotent — safe to call more than once (e.g. from tests).

    Level defaults to INFO but can be raised to DEBUG via the
    `BSA_LOG_LEVEL` environment variable for detailed tracing (per-page
    PDF parsing decisions, column-mapping attempts, matcher candidate
    counts, etc.) without touching code.
    """
    global _configured
    path = log_file_path()
    if _configured:
        return path

    resolved_level = _level_from_env(level if level is not None else logging.INFO)

    root = logging.getLogger()
    root.setLevel(resolved_level)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    _configured = True
    return path
