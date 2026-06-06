"""hermes_cu — Computer Use tool layer for Hermes (no-vision).

A structured computer-use client designed for agents that DO NOT have vision
(the raw screen image is useless to them). Instead we expose:

  * **Perception** (text/JSON): UIA element tree, window list, OCR-friendly
    snapshots, text search. Everything is symbol-level so a text-only LLM
    can decide the next action.
  * **Action** (mouse/keyboard/clipboard): pyautogui-based, with a built-in
    safety layer that blocks operations on blacklisted windows.
  * **Verify**: re-snapshot after an action and diff the relevant subtree.

This package is consumed two ways:
  1. As a FastMCP stdio server (see `server.py`) — loaded by Hermes.
  2. As a CLI (`python -m hermes_cu <cmd>`) — for manual debugging.
"""

from .core import ComputerUse, ActionResult, SnapshotResult
from .safety import SafetyGuard, DEFAULT_BLACKLIST

__version__ = "0.2.0"
__all__ = [
    "ComputerUse",
    "ActionResult",
    "SnapshotResult",
    "SafetyGuard",
    "DEFAULT_BLACKLIST",
    "__version__",
]
