# Changelog

All notable changes to `hermes_cu` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased] - 2026-06-06

## [0.3.0] - 2026-06-06

### Added
- **`screenshot_window`** — Capture a named window to PNG. Uses `mss`
  (fast, no-GPU) with `win32ui.PrintWindow` fallback for GPU-accelerated
  windows. Returns structured result: `gpu_detected`, `content_ratio`,
  `size`, `title`. `content_ratio > 0.1` confirms real content captured.
  - Verified: Edge (content_ratio=0.495), WeChat (content_ratio=0.615)
  - WeChat remains content_ratio=0.039 (Qt engine blocks PrintWindow)
- **`wechat_status`** — Diagnostic tool: check if WeChat is running,
  focused, and what process model is active (float/child_process).
  Intended for hermes_cu pipeline detection of WeChat state before
  screenshot/click actions.

### Known limits documented
- Edge/Chrome/Chromium: no UIA tree (find_text returns `[]`).
  Use `screenshot_window` + vision downstream, or Playwright MCP for DOM.
- Browser automation: use Playwright MCP (`mcp__playwright__*`),
  not hermes_cu. The two tools are complementary.

### Fixed
- **Windows spawn (P-2)**: Node.js `spawn(shell=false)` cannot spawn `.cmd`
  files on Windows → `EINVAL`. Config must use `python.exe -m hermes_cu serve`
  with `PYTHONPATH` env var set, not a `.cmd` wrapper.
  - Affects: mavis daemon MCP config, Hermes config.yaml.
  - Config updated: `python.exe` direct + `PYTHONPATH` env var.

## [0.2.0] - 2026-06-06

### Added
- **Set-of-marks** perception: every clickable element in a snapshot
  receives a sequential `mark` integer. The LLM can refer to elements
  by `mark` instead of parsing coordinates (`click_mark(5)`).
- **`click_mark`** tool — click by mark id. The mark index is
  invalidated by every fresh `screen_snapshot`.
- **`list_marks`** tool — return the mark index from the most recent
  snapshot. Cheap.
- **`drag_to`** tool — drag from (x1,y1) to (x2,y2) with configurable
  duration. Blacklist-checked.
- **Action log** — every action is appended to a JSONL file
  (`./hermes_cu-actions.jsonl` by default, or `$HERMES_CU_LOG`).
  Disable with `log_path=None`. Best-effort, never raises.
- **`snapshot_to_compact`** now prints a clickable index after the tree
  so the agent can scan `[#5] Button 'Save' bounds=[400,300,80,30]`
  without re-parsing the whole tree.

### Changed
- `SnapshotResult.to_dict()` now nests under a `snapshot` key (was
  flat) so the MCP `compact=True` formatter can correctly detect a
  snapshot payload and render the LLM-friendly text version.

## [0.1.0] - 2026-06-06

### Added
- Initial release.
- 14 MCP tools over stdio: `list_windows`, `focus_window`,
  `screen_snapshot`, `find_text`, `wait_for_text`, `screenshot`,
  `click`, `double_click`, `right_click`, `type_text`, `press_key`,
  `hotkey`, `scroll`, `move_to`.
- CLI: `python -m hermes_cu {serve,snapshot,windows,focus,find,click,type,key,hotkey,test,version}`.
- `SafetyGuard` with 23-entry default blacklist (covers browsers,
  chat apps, the Hermes / MiniMax Code window, taskbar).
- `pyautogui.FAILSAFE` enabled — moving the mouse to a screen
  corner aborts the run.
- README + `mcp_config.example.json` + `hermes-cu.bat` wrapper.

[0.2.0]: https://github.com/hermes-contributors/hermes_cu/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/hermes-contributors/hermes_cu/releases/tag/v0.1.0
