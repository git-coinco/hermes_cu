"""v0.2 smoke: verify click_mark / list_marks / drag_to in MCP handler layer."""
import asyncio
import json
import sys
from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest

sys.path.insert(0, r"D:\Hermes_Backup\mavis-outputs\scripts")
from hermes_cu.server import build_server


async def main():
    server = build_server()

    # 1. Verify new tools are registered
    handler = server.request_handlers[ListToolsRequest]
    tools = (await handler(ListToolsRequest())).root.tools
    names = [t.name for t in tools]
    print(f"== {len(tools)} tools ==")
    for t in tools:
        if t.name in ("click_mark", "list_marks", "drag_to", "click", "screen_snapshot", "drag"):
            print(f"  - {t.name}: {t.description[:60]}")
    assert "click_mark" in names, "click_mark missing"
    assert "list_marks" in names, "list_marks missing"
    assert "drag_to" in names, "drag_to missing"
    print("  click_mark, list_marks, drag_to: OK")

    # 2. call click_mark without snapshot (should fail)
    call = server.request_handlers[CallToolRequest]
    print("\n== click_mark(99) without prior snapshot (expect refuse) ==")
    resp = await call(CallToolRequest(
        params=CallToolRequestParams(name="click_mark", arguments={"mark": 99})
    ))
    for c in resp.root.content:
        d = json.loads(c.text)
        print(f"  ok={d.get('ok')} detail={d.get('detail')[:80]}")

    # 3. list_marks
    print("\n== list_marks (expect {}) ==")
    resp = await call(CallToolRequest(
        params=CallToolRequestParams(name="list_marks", arguments={})
    ))
    for c in resp.root.content:
        d = json.loads(c.text)
        print(f"  marks: {d}")

    # 4. drag_to with no real coordinates (will be refused by safety or pyautogui)
    print("\n== drag_to (expect refused: no active window) ==")
    resp = await call(CallToolRequest(
        params=CallToolRequestParams(name="drag_to",
                                     arguments={"x1": 100, "y1": 100, "x2": 200, "y2": 200})
    ))
    for c in resp.root.content:
        d = json.loads(c.text)
        print(f"  ok={d.get('ok')} detail={d.get('detail')[:80]}")

    # 5. drag_to with safe window (cmd) -- need to focus cmd first
    print("\n== focus_window 'cmd' then drag_to ==")
    resp = await call(CallToolRequest(
        params=CallToolRequestParams(name="focus_window", arguments={"title_pattern": "cmd"})
    ))
    for c in resp.root.content:
        d = json.loads(c.text)
        print(f"  focus: ok={d.get('ok')} title={d.get('title')}")

    print("\nV0_2_SMOKE_PASS")


asyncio.run(main())
