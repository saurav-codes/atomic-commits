"""Logging helpers for atc.

Keeps a single Rich console and a debug flag toggled by the CLI.
"""

from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler

console = Console()
err_console = Console(stderr=True)

_LOGGER_NAME = "atc"


def configure(debug: bool = False) -> logging.Logger:
    """Configure and return the atc logger."""
    level = logging.DEBUG if debug else logging.WARNING
    logger = logging.getLogger(_LOGGER_NAME)
    logger.handlers.clear()
    handler = RichHandler(console=err_console, show_time=debug, show_path=debug, markup=True)
    handler.setLevel(level)
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)
