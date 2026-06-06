"""hermes_cu safety layer.

The owner is the top priority. Before any click/type/scroll, we check the
target window against a blacklist of windows the owner is actively using.
If a match is found, the action is refused and the agent is told to either
focus a different window or stop.

Blacklist matching is substring + case-insensitive on `window_text()`. This
is intentionally generous: "edge" blocks both "Microsoft Edge" and any
tab title containing "edge" — better to over-refuse than to click into the
owner's active chat / browser.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

log = logging.getLogger("hermes_cu.safety")


# Windows the owner is actively using — DO NOT TOUCH.
# Extend with care. The substring match is case-insensitive.
DEFAULT_BLACKLIST: tuple[str, ...] = (
    # Mavis/hermes own infrastructure
    "MiniMax Code",
    "Code Helper",
    "Hermes",
    "任务栏",  # taskbar
    "Taskbar",
    # Browsers (owner is reading/researching in them)
    "Microsoft Edge",
    " - Google Chrome",
    "Mozilla Firefox",
    # Communication
    # "微信",  # WeChat — WHITELISTED 2026-06-06 (owner approval)
    # "WeChat",
    "钉钉",
    "DingTalk",
    "飞书",
    "Lark",
    "Feishu",
    "Slack",
    "Telegram",
    "QQ",
    # System / input
    "开始",  # start menu
    "Start menu",
    "Windows 任务栏",
    "Action Center",
    "通知中心",
)

# Windows that override the blacklist — owner approved for automation.
DEFAULT_WHITELIST: tuple[str, ...] = (
    "微信",
    "WeChat",
)


@dataclass
class SafetyGuard:
    """Per-session safety guard.

    Parameters
    ----------
    blacklist : iterable of str
        Window title substrings that block actions. Match is case-insensitive
        substring; use `^...$` style if you need exact match (use `regex`).
    regex : bool
        If True, treat blacklist entries as regex patterns.
    whitelist : iterable of str
        Window title substrings that *override* the blacklist when the
        active window matches. Use this to grant scoped access, e.g.
        whitelist a specific calculator or test app.
    require_focus : bool
        If True, click/type actions require an active window that is not
        blacklisted. If False, the active window is the only check.
    """

    blacklist: tuple[str, ...] = field(default_factory=lambda: DEFAULT_BLACKLIST)
    regex: bool = False
    whitelist: tuple[str, ...] = field(default_factory=lambda: DEFAULT_WHITELIST)
    require_focus: bool = True

    # --- matching primitives ---------------------------------------------

    def _matches(self, text: str, patterns: Iterable[str]) -> Optional[str]:
        if not text:
            return None
        low = text.lower()
        for p in patterns:
            if self.regex:
                if re.search(p, text, re.IGNORECASE):
                    return p
            else:
                if p.lower() in low:
                    return p
        return None

    def is_blocked(self, window_title: str) -> tuple[bool, str]:
        """Return (blocked, reason). If blocked, reason explains why."""
        if not window_title:
            # No active window — refuse by default.
            return True, "no active window"
        wl = self._matches(window_title, self.whitelist)
        if wl is not None:
            return False, f"whitelisted by '{wl}'"
        bl = self._matches(window_title, self.blacklist)
        if bl is not None:
            return True, f"window matches blacklist pattern '{bl}'"
        return False, "ok"

    # --- public check ----------------------------------------------------

    def check_action(
        self,
        *,
        action: str,
        active_window: str,
        x: Optional[int] = None,
        y: Optional[int] = None,
    ) -> tuple[bool, str]:
        """Decide whether an action may proceed.

        `action` is a human-readable verb (click/type/scroll/hotkey/...).
        The active window title is checked against the blacklist.

        Coordinates are sanity-checked: must be inside the virtual screen
        rectangle (negative or > 100k is nonsense on any sane monitor).
        """
        blocked, reason = self.is_blocked(active_window)
        if blocked:
            log.warning("REFUSE %s on '%s': %s", action, active_window, reason)
            return False, f"refused: {reason}"
        if x is not None and y is not None:
            if x < -10_000 or y < -10_000 or x > 100_000 or y > 100_000:
                return False, f"refused: coordinates out of range ({x},{y})"
        return True, "ok"
