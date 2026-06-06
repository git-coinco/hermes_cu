"""hermes_cu CLI.

Usage:
    python -m hermes_cu <command> [args]

Commands:
    serve                Run the MCP stdio server.
    snapshot [--compact] [--title PATTERN] [--depth N]   Print screen_snapshot.
    windows                                          List top-level windows.
    focus <title-substring>                          Focus a window.
    find <text> [--title PATTERN] [--exact]          Search the UIA tree.
    click <x> <y>                                    Click (blacklist check).
    type <text>                                      Type (blacklist check).
    key <name>                                       Press a key.
    hotkey <k1> [k2 ...]                             Press combination.
    test                                             Run a self-test (no clicks).
    version                                          Print version.
"""

from __future__ import annotations

import argparse
import json
import sys

from .core import ComputerUse, snapshot_to_compact


def cmd_serve(_args) -> int:
    from .server import main as server_main
    server_main()
    return 0


def cmd_snapshot(args, cu: ComputerUse) -> int:
    snap = cu.screen_snapshot(
        title_pattern=args.title, max_depth=args.depth, max_children=args.children,
    ).to_dict()
    if args.compact:
        print(snapshot_to_compact(snap))
    else:
        print(json.dumps(snap, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_windows(_args, cu: ComputerUse) -> int:
    print(json.dumps(cu.list_windows(), ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_focus(args, cu: ComputerUse) -> int:
    print(json.dumps(cu.focus_window(args.title).to_dict(), ensure_ascii=False, indent=2))
    return 0 if cu else 1  # always ok; we just printed


def cmd_find(args, cu: ComputerUse) -> int:
    res = cu.find_text(
        text=args.text, title_pattern=args.title, exact=args.exact,
        max_depth=args.depth, max_results=args.limit,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_click(args, cu: ComputerUse) -> int:
    r = cu.click(args.x, args.y, button=args.button, clicks=args.clicks)
    print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
    return 0 if r.ok else 2


def cmd_click_mark(args, cu: ComputerUse) -> int:
    r = cu.click_mark(args.mark, button=args.button, clicks=args.clicks)
    print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
    return 0 if r.ok else 2


def cmd_drag_to(args, cu: ComputerUse) -> int:
    r = cu.drag_to(args.x1, args.y1, args.x2, args.y2, duration=args.duration, button=args.button)
    print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
    return 0 if r.ok else 2


def cmd_list_marks(_args, cu: ComputerUse) -> int:
    print(json.dumps(cu.last_marks, ensure_ascii=False, indent=2))
    return 0


def cmd_type(args, cu: ComputerUse) -> int:
    r = cu.type_text(args.text)
    print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
    return 0 if r.ok else 2


def cmd_key(args, cu: ComputerUse) -> int:
    r = cu.press_key(args.key)
    print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
    return 0 if r.ok else 2


def cmd_hotkey(args, cu: ComputerUse) -> int:
    r = cu.hotkey(*args.keys)
    print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
    return 0 if r.ok else 2


def cmd_test(_args, cu: ComputerUse) -> int:
    """Run a self-test: list windows, snapshot, find a known text — without clicking."""
    print("== screen size ==")
    from . import __version__
    print(f"hermes_cu {__version__}")
    print(f"size: {cu.screen_w}x{cu.screen_h}")
    print()
    print("== windows ==")
    wins = cu.list_windows()
    print(f"{len(wins)} windows visible")
    for w in wins[:8]:
        print(f"  - {w['title'][:60]!r}  bounds={w['bounds']}  pid={w['pid']}")
    print()
    print("== active window ==")
    aw = cu._active_window_title()  # noqa: SLF001
    print(f"active: {aw!r}")
    blocked, reason = cu.guard.is_blocked(aw)
    print(f"safety: blocked={blocked}  reason={reason}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hermes_cu",
        description="Computer-use client for Hermes (no-vision).",
    )
    sub = parser.add_subparsers(dest="cmd", required=False)

    sub.add_parser("serve", help="Run the MCP stdio server.")
    sub.add_parser("windows", help="List top-level windows.")
    sub.add_parser("test", help="Run a self-test (no clicks).")
    sub.add_parser("version", help="Print version.")

    p_snap = sub.add_parser("snapshot", help="Print a screen snapshot.")
    p_snap.add_argument("--compact", action="store_true")
    p_snap.add_argument("--title", default=None)
    p_snap.add_argument("--depth", type=int, default=4)
    p_snap.add_argument("--children", type=int, default=30)

    p_focus = sub.add_parser("focus", help="Focus a window by title substring.")
    p_focus.add_argument("title")

    p_find = sub.add_parser("find", help="Search the UIA tree for a name.")
    p_find.add_argument("text")
    p_find.add_argument("--title", default=None)
    p_find.add_argument("--exact", action="store_true")
    p_find.add_argument("--depth", type=int, default=6)
    p_find.add_argument("--limit", type=int, default=10)

    p_click = sub.add_parser("click", help="Click at absolute coordinates.")
    p_click.add_argument("x", type=int)
    p_click.add_argument("y", type=int)
    p_click.add_argument("--button", default="left", choices=["left", "right", "middle"])
    p_click.add_argument("--clicks", type=int, default=1)

    p_cm = sub.add_parser("click-mark", help="Click a set-of-marks id from the last snapshot.")
    p_cm.add_argument("mark", type=int)
    p_cm.add_argument("--button", default="left", choices=["left", "right", "middle"])
    p_cm.add_argument("--clicks", type=int, default=1)

    sub.add_parser("marks", help="List set-of-marks from the last snapshot.")

    p_drag = sub.add_parser("drag", help="Drag from (x1,y1) to (x2,y2).")
    p_drag.add_argument("x1", type=int)
    p_drag.add_argument("y1", type=int)
    p_drag.add_argument("x2", type=int)
    p_drag.add_argument("y2", type=int)
    p_drag.add_argument("--duration", type=float, default=0.4)
    p_drag.add_argument("--button", default="left", choices=["left", "right", "middle"])

    p_type = sub.add_parser("type", help="Type into the focused input.")
    p_type.add_argument("text")

    p_key = sub.add_parser("key", help="Press a single key.")
    p_key.add_argument("key")

    p_hk = sub.add_parser("hotkey", help="Press a key combination.")
    p_hk.add_argument("keys", nargs="+")

    args = parser.parse_args(argv)
    cu = ComputerUse()
    if args.cmd in (None, "version"):
        from . import __version__
        print(__version__)
        return 0
    if args.cmd == "serve":
        return cmd_serve(args)
    if args.cmd == "snapshot":
        return cmd_snapshot(args, cu)
    if args.cmd == "windows":
        return cmd_windows(args, cu)
    if args.cmd == "focus":
        return cmd_focus(args, cu)
    if args.cmd == "find":
        return cmd_find(args, cu)
    if args.cmd == "click":
        return cmd_click(args, cu)
    if args.cmd == "click-mark":
        return cmd_click_mark(args, cu)
    if args.cmd == "marks":
        return cmd_list_marks(args, cu)
    if args.cmd == "drag":
        return cmd_drag_to(args, cu)
    if args.cmd == "type":
        return cmd_type(args, cu)
    if args.cmd == "key":
        return cmd_key(args, cu)
    if args.cmd == "hotkey":
        return cmd_hotkey(args, cu)
    if args.cmd == "test":
        return cmd_test(args, cu)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
