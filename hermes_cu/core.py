"""hermes_cu core: perception + action for the Hermes agent.

This is the only file that touches the screen. Everything else is a thin
wrapper.

Design constraints
------------------
* The caller is an LLM without vision. Every "what's on screen" answer is
  a structured JSON object — never an image blob.
* pyautogui is the execution layer. UIA via pywinauto is the perception
  layer. mss is the verification layer (optional, used only for
  `screenshot()` which is rarely needed by the agent itself).
* `pyautogui.FAILSAFE` and `pyautogui.PAUSE` are tuned for a tightly
  supervised agent: moving the mouse to a screen corner raises FailSafe
  (the owner can abort with a flick of the wrist) and a small pause keeps
  actions deterministic.
* **Set-of-marks** (Anthropic CU): every clickable element in a snapshot
  gets a sequential integer `mark`. The agent can refer to elements by
  mark (`click_mark(5)`) instead of parsing coordinates. See
  `_walk_element` and the `marks` index in `screen_snapshot`.
* **Action log**: every action is appended to a JSONL file for replay,
  audit, and crash forensics. Disable with `log_path=None`.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pyautogui

from .safety import SafetyGuard, DEFAULT_BLACKLIST

# pywinauto is a heavy import; do it lazily so the CLI stays snappy
# when the agent only wants to call e.g. `click`.
def _import_pywinauto():
    from pywinauto import Desktop  # noqa: WPS433
    return Desktop

def _import_mss():
    import mss  # noqa: WPS433
    return mss

log = logging.getLogger("hermes_cu.core")

# Tighten pyautogui defaults for an autonomous agent.
pyautogui.FAILSAFE = True   # moving mouse to a corner aborts
pyautogui.PAUSE = 0.05      # 50ms between actions
pyautogui.MINIMUM_DURATION = 0.05


@dataclass
class ActionResult:
    ok: bool
    action: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "action": self.action, "detail": self.detail, **self.data}


@dataclass
class SnapshotResult:
    ok: bool
    snapshot: dict[str, Any]
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": self.ok, "snapshot": dict(self.snapshot)}
        if self.error:
            out["error"] = self.error
        return out


# ---------------------------------------------------------------------------
# Element-tree walker (UIA → LLM-friendly dict)
# ---------------------------------------------------------------------------

# Control types we expose to the agent. Anything else falls under "Other".
_KNOWN_CONTROL_TYPES = {
    "Window", "Pane", "Document", "Group", "Tab", "TabItem",
    "Button", "Hyperlink", "Edit", "CheckBox", "RadioButton",
    "ComboBox", "List", "ListItem", "Menu", "MenuItem", "MenuBar",
    "Tree", "TreeItem", "Table", "DataItem", "Header", "HeaderItem",
    "Text", "Image", "ProgressBar", "Slider", "Spinner",
    "StatusBar", "ToolBar", "ToolTip", "TitleBar", "ScrollBar",
}


def _is_clickable(control_type: str) -> bool:
    """Heuristic: which UIA control types accept a click?"""
    return control_type in {
        "Button", "Hyperlink", "CheckBox", "RadioButton",
        "TabItem", "MenuItem", "ListItem", "TreeItem", "DataItem",
        "HeaderItem", "Image",
    }


def _is_text_input(control_type: str) -> bool:
    return control_type in {"Edit", "Document", "ComboBox"}


def _walk_element(element, depth: int, max_depth: int, max_children: int,
                  mark_counter: list[int] | None = None) -> dict[str, Any]:
    """Recursively turn a UIA element into a JSON-friendly dict.

    Bounds are returned in the form `[x, y, w, h]` so the agent can both
    click the center (`x + w/2, y + h/2`) and reason about layout.

    If `mark_counter` is provided (a one-element list `[int]`), every
    clickable element receives a sequential `mark` integer and the counter
    is incremented. The caller is expected to keep a parallel index of
    `mark → (name, type, bounds, center)` so the agent can `click_mark(5)`.
    """
    try:
        ct = element.control_type or "Other"
    except Exception:
        ct = "Other"

    try:
        name = element.name or ""
    except Exception:
        name = ""

    try:
        value = element.value if element.value != "" else None
    except Exception:
        value = None

    try:
        rect = element.rectangle
        bounds = [int(rect.left), int(rect.top), int(rect.width()), int(rect.height())]
    except Exception:
        bounds = [0, 0, 0, 0]

    try:
        enabled = bool(element.is_enabled())
    except Exception:
        enabled = True

    node: dict[str, Any] = {
        "type": ct,
        "name": name,
        "bounds": bounds,
        "enabled": enabled,
    }
    if value is not None:
        node["value"] = str(value)[:200]
    if _is_clickable(ct):
        node["clickable"] = True
    if _is_text_input(ct):
        node["text_input"] = True
    # Auto-classify anything with a name and a non-zero area as clickable
    # as a fallback — many apps don't expose control_type properly.
    if "clickable" not in node and name and bounds[2] > 5 and bounds[3] > 5:
        node["clickable"] = True

    # Set-of-marks: assign a sequential id to every clickable element
    # with a non-empty name. Unnamed buttons are skipped (too ambiguous).
    if mark_counter is not None and node.get("clickable") and name:
        mark_counter[0] += 1
        node["mark"] = mark_counter[0]

    if depth >= max_depth:
        node["truncated"] = True
        return node

    try:
        children = element.children()
    except Exception:
        children = []

    out_children: list[dict[str, Any]] = []
    for i, ch in enumerate(children):
        if i >= max_children:
            out_children.append({"truncated": True, "more": len(children) - i})
            break
        out_children.append(_walk_element(ch, depth + 1, max_depth, max_children, mark_counter))
    if out_children:
        node["children"] = out_children
    return node


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class ComputerUse:
    """The single entry point. Use as a context manager for clean shutdown."""

    def __init__(
        self,
        *,
        guard: Optional[SafetyGuard] = None,
        screen_w: Optional[int] = None,
        screen_h: Optional[int] = None,
        log_path: Optional[str | Path] = None,
    ) -> None:
        self.guard = guard or SafetyGuard()
        size = pyautogui.size()
        self.screen_w = screen_w or int(size.width)
        self.screen_h = screen_h or int(size.height)
        self._desktop = None  # lazy
        # Action log: append-only JSONL. Default: ./hermes_cu-actions.jsonl
        # unless the env var HERMES_CU_LOG points elsewhere.
        if log_path is None:
            env = os.environ.get("HERMES_CU_LOG")
            log_path = Path(env) if env else Path("hermes_cu-actions.jsonl")
        self.log_path = Path(log_path)
        # Last snapshot's mark index (mark -> {name, type, bounds, center}).
        # Fresh on every screen_snapshot. The agent uses this with click_mark.
        self.last_marks: dict[int, dict[str, Any]] = {}

    # --- lazy handles ---------------------------------------------------

    @property
    def desktop(self):
        if self._desktop is None:
            Desktop = _import_pywinauto()
            self._desktop = Desktop(backend="uia")
        return self._desktop

    # --- helpers --------------------------------------------------------

    def _active_window_title(self) -> str:
        try:
            win = pyautogui.getActiveWindow()
            return (win.title if win else "") or ""
        except Exception:
            return ""

    def _find_window(self, title_pattern: str):
        """Return the first window whose title matches a substring (case-insensitive).

        Raises ValueError if not found.
        """
        title_low = title_pattern.lower()
        for w in self.desktop.windows():
            try:
                t = w.window_text() or ""
            except Exception:
                continue
            if title_low in t.lower():
                return w
        raise ValueError(f"no window matches title pattern '{title_pattern}'")

    # =====================================================================
    # PERCEPTION
    # =====================================================================

    def list_windows(self) -> list[dict[str, Any]]:
        """Return a flat list of top-level windows visible to UIA.

        Cheap; safe to call before every action.
        """
        out: list[dict[str, Any]] = []
        for w in self.desktop.windows():
            try:
                title = w.window_text() or ""
            except Exception:
                title = ""
            try:
                rect = w.rectangle
                bounds = [int(rect.left), int(rect.top), int(rect.width()), int(rect.height())]
            except Exception:
                bounds = [0, 0, 0, 0]
            try:
                pid = w.process_id()
            except Exception:
                pid = None
            try:
                visible = w.is_visible()
            except Exception:
                visible = True
            if not title and bounds == [0, 0, 0, 0]:
                continue
            out.append({
                "title": title,
                "pid": pid,
                "bounds": bounds,
                "visible": visible,
            })
        return out

    def focus_window(self, title_pattern: str) -> ActionResult:
        """Bring the first matching window to the foreground."""
        try:
            w = self._find_window(title_pattern)
        except ValueError as e:
            return ActionResult(ok=False, action="focus_window", detail=str(e))
        try:
            # set_focus() can fail on UAC-protected windows; wrap.
            w.set_focus()
            time.sleep(0.1)
            return ActionResult(ok=True, action="focus_window",
                                detail=f"focused '{w.window_text()}'",
                                data={"title": w.window_text()})
        except Exception as e:
            return ActionResult(ok=False, action="focus_window", detail=f"focus failed: {e}")

    def screen_snapshot(
        self,
        *,
        title_pattern: Optional[str] = None,
        max_depth: int = 4,
        max_children: int = 30,
    ) -> SnapshotResult:
        """Return a JSON-serializable description of the screen.

        If `title_pattern` is given, the deep element tree is rooted at the
        first matching window. Otherwise the deep tree is rooted at the
        active window. Either way, the flat `windows` list is included so
        the agent can see the world.
        """
        snap: dict[str, Any] = {
            "ts": time.time(),
            "screen": {"width": self.screen_w, "height": self.screen_h},
            "active_window": self._active_window_title(),
            "windows": self.list_windows(),
        }
        # Decide which window to deep-walk.
        target = None
        try:
            if title_pattern:
                target = self._find_window(title_pattern)
            else:
                aw = pyautogui.getActiveWindow()
                if aw:
                    for w in self.desktop.windows():
                        try:
                            if w.window_text() == aw.title:
                                target = w
                                break
                        except Exception:
                            continue
        except Exception as e:
            log.warning("snapshot: window resolve failed: %s", e)

        if target is not None:
            try:
                mark_counter: list[int] = [0]
                snap["ui_tree"] = _walk_element(target, 0, max_depth, max_children, mark_counter)
            except Exception as e:
                snap["ui_tree"] = None
                snap["ui_tree_error"] = str(e)
                mark_counter = None
        else:
            snap["ui_tree"] = None
            mark_counter = None

        # Collect marks -> {name, type, bounds, center} for click_mark().
        # Walk the tree we just produced (no need to re-query UIA).
        if mark_counter is not None and snap["ui_tree"] is not None:
            self.last_marks = self._collect_marks(snap["ui_tree"])
            snap["marks"] = self.last_marks
            snap["mark_count"] = len(self.last_marks)
        else:
            self.last_marks = {}
            snap["marks"] = {}
            snap["mark_count"] = 0

        return SnapshotResult(ok=True, snapshot=snap)

    @staticmethod
    def _collect_marks(node: dict[str, Any], out: Optional[dict[int, dict[str, Any]]] = None) -> dict[int, dict[str, Any]]:
        """Walk a snapshot tree and index every node that has a `mark` field."""
        if out is None:
            out = {}
        mark = node.get("mark")
        if mark is not None:
            b = node.get("bounds", [0, 0, 0, 0])
            out[mark] = {
                "name": node.get("name", ""),
                "type": node.get("type", "Other"),
                "bounds": b,
                "center": [b[0] + b[2] // 2, b[1] + b[3] // 2],
            }
        for ch in node.get("children", []) or []:
            ComputerUse._collect_marks(ch, out)
        return out

    def find_text(
        self,
        text: str,
        *,
        title_pattern: Optional[str] = None,
        exact: bool = False,
        max_depth: int = 6,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Search the UIA tree for elements whose name contains `text`.

        Returns up to `max_results` matches with bounds so the agent can
        click the center of the first one.
        """
        if title_pattern:
            root = self._find_window(title_pattern)
        else:
            aw = pyautogui.getActiveWindow()
            root = None
            if aw:
                for w in self.desktop.windows():
                    try:
                        if w.window_text() == aw.title:
                            root = w
                            break
                    except Exception:
                        continue
            if root is None:
                return []

        needle = text.lower()
        results: list[dict[str, Any]] = []

        def visit(node) -> None:
            if len(results) >= max_results:
                return
            try:
                name = (node.name or "")
            except Exception:
                name = ""
            if name and ((exact and name.lower() == needle) or (not exact and needle in name.lower())):
                try:
                    rect = node.rectangle
                    bounds = [int(rect.left), int(rect.top), int(rect.width()), int(rect.height())]
                except Exception:
                    bounds = [0, 0, 0, 0]
                try:
                    ct = node.control_type or "Other"
                except Exception:
                    ct = "Other"
                cx = bounds[0] + bounds[2] // 2
                cy = bounds[1] + bounds[3] // 2
                results.append({
                    "name": name,
                    "type": ct,
                    "bounds": bounds,
                    "center": [cx, cy],
                })
            try:
                for ch in node.children():
                    visit(ch)
            except Exception:
                pass

        visit(root)
        return results

    def wait_for_text(
        self,
        text: str,
        *,
        timeout: float = 10.0,
        title_pattern: Optional[str] = None,
        exact: bool = False,
        poll_interval: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Poll `find_text` until something appears or the timeout fires.

        Returns the matches found at the end (possibly empty).
        """
        deadline = time.time() + timeout
        last: list[dict[str, Any]] = []
        while time.time() < deadline:
            last = self.find_text(text, title_pattern=title_pattern, exact=exact)
            if last:
                return last
            time.sleep(poll_interval)
        return last

    def screenshot(self, path: str) -> ActionResult:
        """Save a screenshot of the primary monitor to `path`."""
        mss = _import_mss()
        try:
            with mss.mss() as sct:
                img = sct.grab(sct.monitors[1])
                # mss returns BGRA; PIL wants that.
                from PIL import Image  # noqa: WPS433
                Image.frombytes("RGB", img.size, img.bgra[..., :3].tobytes() if hasattr(img.bgra, "tobytes") else bytes(img.bgra)[:img.size[0] * img.size[1] * 3]).save(path)
            return ActionResult(ok=True, action="screenshot", detail=path, data={"path": path})
        except Exception as e:
            return ActionResult(ok=False, action="screenshot", detail=f"screenshot failed: {e}")

    # =====================================================================
    # ACTION
    # =====================================================================

    def _guard_action(self, action: str, x: Optional[int] = None, y: Optional[int] = None) -> Optional[ActionResult]:
        aw = self._active_window_title()
        ok, reason = self.guard.check_action(action=action, active_window=aw, x=x, y=y)
        if not ok:
            return ActionResult(ok=False, action=action, detail=reason,
                                data={"active_window": aw})
        return None

    def _log_action(self, action: str, args: dict[str, Any], result: ActionResult) -> None:
        """Append an action record to the JSONL log. Best-effort, never raises."""
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": time.time(),
                "iso": datetime.now().isoformat(timespec="seconds"),
                "action": action,
                "args": args,
                "ok": result.ok,
                "detail": result.detail,
                "active_window": result.data.get("active_window"),
            }
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            log.warning("action log failed: %s", e)

    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> ActionResult:
        refusal = self._guard_action("click", x, y)
        if refusal:
            self._log_action("click", {"x": x, "y": y, "button": button, "clicks": clicks}, refusal)
            return refusal
        try:
            pyautogui.click(x=x, y=y, button=button, clicks=clicks)
            r = ActionResult(ok=True, action="click",
                             detail=f"({x},{y}) button={button} clicks={clicks}",
                             data={"x": x, "y": y, "button": button, "clicks": clicks,
                                   "active_window": self._active_window_title()})
        except Exception as e:
            r = ActionResult(ok=False, action="click", detail=str(e))
        self._log_action("click", {"x": x, "y": y, "button": button, "clicks": clicks}, r)
        return r

    def click_mark(self, mark: int, *, button: str = "left", clicks: int = 1) -> ActionResult:
        """Click a set-of-marks id from the last snapshot.

        Much more reliable than parsing coordinates — the LLM sees
        `[#5] Button 'Save' bounds=[400,300,80,30]` in the snapshot and
        just calls `click_mark(5)`. Marks are invalidated by the next
        `screen_snapshot` (the tree may have moved).
        """
        if not self.last_marks:
            r = ActionResult(ok=False, action="click_mark",
                             detail="no marks available; call screen_snapshot first")
            self._log_action("click_mark", {"mark": mark}, r)
            return r
        info = self.last_marks.get(mark)
        if info is None:
            r = ActionResult(ok=False, action="click_mark",
                             detail=f"unknown mark {mark}; available: {sorted(self.last_marks)[:10]}{'...' if len(self.last_marks) > 10 else ''}",
                             data={"available": sorted(self.last_marks)})
            self._log_action("click_mark", {"mark": mark}, r)
            return r
        cx, cy = info["center"]
        # Delegate to click() so the safety guard + log run normally.
        r = self.click(cx, cy, button=button, clicks=clicks)
        # Re-tag the action name so logs distinguish coord vs mark clicks.
        r.action = "click_mark"
        r.data["mark"] = mark
        r.data["marked_element"] = info
        return r

    def double_click(self, x: int, y: int) -> ActionResult:
        return self.click(x, y, clicks=2)

    def right_click(self, x: int, y: int) -> ActionResult:
        return self.click(x, y, button="right")

    def drag_to(self, x1: int, y1: int, x2: int, y2: int, *, duration: float = 0.4,
                button: str = "left") -> ActionResult:
        """Drag from (x1,y1) to (x2,y2). pyautogui.mouseDown/mouseUp with a slow move."""
        refusal = self._guard_action("drag_to", x2, y2)
        if refusal:
            self._log_action("drag_to",
                             {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration": duration, "button": button},
                             refusal)
            return refusal
        try:
            pyautogui.moveTo(x1, y1)
            pyautogui.mouseDown(button=button)
            pyautogui.moveTo(x2, y2, duration=duration)
            pyautogui.mouseUp(button=button)
            r = ActionResult(ok=True, action="drag_to",
                             detail=f"({x1},{y1}) -> ({x2},{y2}) duration={duration}s button={button}",
                             data={"x1": x1, "y1": y1, "x2": x2, "y2": y2,
                                   "duration": duration, "button": button,
                                   "active_window": self._active_window_title()})
        except Exception as e:
            r = ActionResult(ok=False, action="drag_to", detail=str(e))
        self._log_action("drag_to",
                         {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration": duration, "button": button},
                         r)
        return r

    def type_text(self, text: str, *, interval: float = 0.02) -> ActionResult:
        refusal = self._guard_action("type_text")
        if refusal:
            self._log_action("type_text", {"text": text, "interval": interval}, refusal)
            return refusal
        try:
            # write() uses clipboard for unicode safety on Windows; falls
            # back to typewrite for ASCII if clipboard fails.
            try:
                import pyperclip  # noqa: WPS433
                pyperclip.copy(text)
                pyautogui.hotkey("ctrl", "v")
            except Exception:
                pyautogui.typewrite(text, interval=interval)
            r = ActionResult(ok=True, action="type_text",
                             detail=f"typed {len(text)} chars",
                             data={"length": len(text),
                                   "active_window": self._active_window_title()})
        except Exception as e:
            r = ActionResult(ok=False, action="type_text", detail=str(e))
        self._log_action("type_text", {"text": text, "interval": interval}, r)
        return r

    def press_key(self, key: str) -> ActionResult:
        refusal = self._guard_action("press_key")
        if refusal:
            self._log_action("press_key", {"key": key}, refusal)
            return refusal
        try:
            pyautogui.press(key)
            r = ActionResult(ok=True, action="press_key", detail=key, data={"key": key})
        except Exception as e:
            r = ActionResult(ok=False, action="press_key", detail=str(e))
        self._log_action("press_key", {"key": key}, r)
        return r

    def hotkey(self, *keys: str) -> ActionResult:
        refusal = self._guard_action("hotkey")
        if refusal:
            self._log_action("hotkey", {"keys": list(keys)}, refusal)
            return refusal
        try:
            pyautogui.hotkey(*keys)
            r = ActionResult(ok=True, action="hotkey",
                             detail="+".join(keys), data={"keys": list(keys)})
        except Exception as e:
            r = ActionResult(ok=False, action="hotkey", detail=str(e))
        self._log_action("hotkey", {"keys": list(keys)}, r)
        return r

    def scroll(self, x: int, y: int, dx: int = 0, dy: int = -3) -> ActionResult:
        refusal = self._guard_action("scroll", x, y)
        if refusal:
            self._log_action("scroll", {"x": x, "y": y, "dx": dx, "dy": dy}, refusal)
            return refusal
        try:
            pyautogui.moveTo(x, y)
            if dy:
                pyautogui.scroll(dy)
            if dx:
                pyautogui.hscroll(dx)
            r = ActionResult(ok=True, action="scroll",
                             detail=f"({x},{y}) dx={dx} dy={dy}",
                             data={"x": x, "y": y, "dx": dx, "dy": dy})
        except Exception as e:
            r = ActionResult(ok=False, action="scroll", detail=str(e))
        self._log_action("scroll", {"x": x, "y": y, "dx": dx, "dy": dy}, r)
        return r

    def move_to(self, x: int, y: int) -> ActionResult:
        # No safety check — moving the mouse is harmless. Useful for hover.
        try:
            pyautogui.moveTo(x, y)
            r = ActionResult(ok=True, action="move_to", detail=f"({x},{y})", data={"x": x, "y": y})
        except Exception as e:
            r = ActionResult(ok=False, action="move_to", detail=str(e))
        self._log_action("move_to", {"x": x, "y": y}, r)
        return r


# ---------------------------------------------------------------------------
# Convenience: pretty-print snapshots for the LLM
# ---------------------------------------------------------------------------


def snapshot_to_compact(snap: dict[str, Any]) -> str:
    """Render a snapshot dict as a single human-readable string.

    The LLM sees this and reasons about what to do next. We strip noise
    (zero-bounds, empty names) and use indentation to show tree depth.
    """
    lines: list[str] = []
    screen = snap.get("screen", {})
    lines.append(f"# Screen {screen.get('width')}x{screen.get('height')}  active='{snap.get('active_window','')}'")

    wins = snap.get("windows", [])
    if wins:
        lines.append(f"\n# {len(wins)} windows:")
        for w in wins:
            t = (w.get("title") or "(no title)").replace("\n", " ")
            b = w.get("bounds", [0, 0, 0, 0])
            lines.append(f"  - {t}  bounds={b}  pid={w.get('pid')}")

    tree = snap.get("ui_tree")
    if tree is None:
        lines.append("\n# (no active window ui tree)")
        return "\n".join(lines)

    def render(node: dict[str, Any], depth: int) -> None:
        if not node:
            return
        b = node.get("bounds", [0, 0, 0, 0])
        if b[2] == 0 or b[3] == 0:
            return  # invisible
        name = (node.get("name") or "").replace("\n", " ")
        if not name and not node.get("value"):
            return  # unlabeled zero-content
        flags = []
        if node.get("clickable"):
            flags.append("click")
        if node.get("text_input"):
            flags.append("input")
        flag_str = (" [" + ",".join(flags) + "]") if flags else ""
        mark_str = f" [#{node['mark']}]" if node.get("mark") else ""
        indent = "  " * depth
        lines.append(f"{indent}- {node.get('type','?')}: {name!r}  bounds={b}{flag_str}{mark_str}")
        for ch in node.get("children", []) or []:
            render(ch, depth + 1)

    lines.append("\n# UI tree:")
    render(tree, 0)

    # Append a quick "clickable index" so the agent can `click_mark(5)`
    # without having to scan the tree.
    marks = snap.get("marks", {})
    if marks:
        lines.append("\n# Clickable index (use click_mark(<id>)):")
        for mid in sorted(marks):
            m = marks[mid]
            cx, cy = m["center"]
            lines.append(f"  #{mid:<3} {m['type']:<10} {m['name'][:40]!r:<42} center=({cx},{cy})")

    return "\n".join(lines)
