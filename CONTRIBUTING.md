# How to contribute

Issues and pull requests are welcome. Before opening a PR:

1. Run the smoke test: `python -m hermes_cu test`
2. Run the MCP handler test: `python tests/_mcp_smoke.py`
3. Update `CHANGELOG.md` with your change under an "Unreleased" section.

## Design principles

* **Text-first perception.** No image data crosses the agent boundary.
* **Deterministic safety.** Every action is gated by `SafetyGuard`; the
  `pyautogui.FAILSAFE` corner-of-screen abort is the last-resort kill
  switch. New tools should never bypass this.
* **Set-of-marks by default.** Coordinate-based clicks are error-prone.
  New perception tools should populate `mark_counter` so the agent
  always has a `click_mark` option.
* **Log every action.** The JSONL log is the user's audit trail and
  the maintainer's crash-recovery fallback.
* **No hidden network calls.** All I/O is local: Windows UIA, mouse /
  keyboard via pyautogui, screenshots via mss.

## Adding a new tool

1. Implement the action in `hermes_cu/core.py` on the `ComputerUse`
   class. Use `_guard_action(...)` to enforce the safety blacklist and
   `_log_action(...)` to record the action.
2. Register the tool in `hermes_cu/server.py` (add a `Tool(...)` to
   `TOOLS` and a branch in `call_tool`).
3. Optionally expose a CLI subcommand in `hermes_cu/__main__.py`.
4. Add a smoke test under `tests/`.
5. Update `README.md` tool table and `CHANGELOG.md`.
