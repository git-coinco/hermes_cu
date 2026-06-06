# hermes_cu

> A **no-vision** computer-use client for text-only LLM agents on Windows.
> Inspired by [Anthropic Computer Use](https://docs.anthropic.com/en/docs/agents-and-tools/computer-use) and
> [Open Interpreter](https://github.com/OpenInterpreter/open-interpreter), but designed for
> agents that **cannot see images**. Every "what's on screen" answer is a
> structured JSON object — control type, name, bounds, clickable flag, and
> (in v0.2) a set-of-marks integer.

## Why no-vision?

Most agents (Hermes included) operate on text-only language models. Sending
them a 1920×1080 PNG every turn burns the context window on data the model
cannot use. Instead, `hermes_cu` walks the Windows **UIA** (UI Automation)
tree and returns a compact, LLM-friendly description of the screen.

```
# Screen 1920x1080  active='Calculator'

# 5 windows:
  - Calculator  bounds=[100, 100, 400, 600]  pid=1234
  - ...

# UI tree:
- Window: 'Calculator'  bounds=[100, 100, 400, 600]
  - Pane: ''  bounds=[110, 130, 380, 560]
    - Button: '1'  bounds=[120, 460, 80, 60] [click] [#1]
    - Button: '2'  bounds=[200, 460, 80, 60] [click] [#2]
    ...

# Clickable index (use click_mark(<id>)):
  #1   Button     '1'                       center=(160,490)
  #2   Button     '2'                       center=(240,490)
  ...
```

The agent responds with an action — and `hermes_cu` makes sure that action
happens on the real screen, with a built-in safety guard that refuses
operations on the owner's active windows (browser, chat, the editor window
itself).

## What you get

- **17 MCP tools** over stdio (Model Context Protocol). Hermes or any
  MCP-compatible client can call `mcp__hermes_cu__*` directly:

  | # | Tool | Category | Description |
  |---|------|----------|-------------|
  | 1 | `list_windows` | Perception | List all top-level windows (title, pid, bounds, visibility) |
  | 2 | `focus_window` | Perception | Bring a window to foreground by title pattern |
  | 3 | `screen_snapshot` | Perception | Structured UIA tree of active/named window + all windows |
  | 4 | `find_text` | Perception | Search UIA tree for elements by text, returns bounds + center |
  | 5 | `wait_for_text` | Perception | Poll `find_text` until match appears or timeout fires |
  | 6 | `list_marks` | Perception | Return set-of-marks index from most recent snapshot |
  | 7 | `screenshot` | Verify | Save PNG screenshot (for vision-capable downstream tools) |
  | 8 | `screenshot_window` | Verify | Screenshot a named window with GPU-acceleration detection |
  | 9 | `click` | Action | Click at absolute (x, y) coordinates |
  | 10 | `click_mark` | Action | Click by set-of-marks integer id (e.g. `#5`) |
  | 11 | `double_click` | Action | Double-click at (x, y) |
  | 12 | `right_click` | Action | Right-click at (x, y) |
  | 13 | `drag_to` | Action | Drag from (x1,y1) to (x2,y2) over duration seconds |
  | 14 | `type_text` | Action | Type unicode text via clipboard paste |
  | 15 | `press_key` | Action | Press a single key (enter, tab, esc, f5, etc.) |
  | 16 | `hotkey` | Action | Press key combination (e.g. ctrl+c, alt+tab) |
  | 17 | `scroll` | Action | Move cursor to (x,y) and scroll (dy>0=up) |
  | 18 | `move_to` | Action | Move cursor to (x, y) — no blacklist check |
  | 19 | `clipboard_read` | System | Read text from Windows clipboard (tkinter) |
  | 20 | `clipboard_write` | System | Write text to Windows clipboard (tkinter) |
  | 21 | `window_control` | System | Minimize / maximize / restore / close / show / hide a window |
  | 22 | `list_processes` | System | List top N processes by memory usage (tasklist + psapi) |
  | 23 | `browse_dir` | System | List directory contents with type, size, modified time |
  | 24 | `read_file` | System | Read text file (up to max_bytes, truncated flag) |
  | 25 | `write_file` | System | Write text to file, creates parent dirs if needed |
  | 26 | `run_command` | System | Run shell command, return stdout + stderr + returncode |
  | 27 | `wechat_status` | WeChat | Check WeChat window state and GPU-render detection |

  > **Set-of-marks** (v0.2+): every clickable element in `screen_snapshot` gets a
  > sequential `[#N]` id. Prefer `click_mark(N)` over raw `click(x, y)` —
  > fewer parsing errors, faster LLM decisions.

- **Set-of-marks** (v0.2): every clickable element in a snapshot gets
  a sequential id. The LLM says `click_mark(5)` instead of parsing
  coordinates — fewer errors, faster decisions, less context.
- **Action log** (v0.2): every action is appended to a JSONL file for
  replay, audit, and crash forensics. Default `./hermes_cu-actions.jsonl`.
- **Safety guard** with a 23-entry default blacklist (browsers, chat
  apps, the MiniMax Code / Hermes window itself, the taskbar). The
  `pyautogui.FAILSAFE` corner-of-screen abort is also on.
- **CLI** for debugging: `python -m hermes_cu {snapshot,windows,click,type,...}`.

## Install

```powershell
python -m pip install hermes-cu
# or, from source:
git clone https://github.com/hermes-contributors/hermes_cu
cd hermes_cu
python -m pip install -e .
```

Requires Windows 10/11 and Python ≥ 3.11.

## Use as an MCP server

```json
{
  "mcpServers": {
    "hermes_cu": {
      "command": "C:/Users/CLL/.hermes/hermes-agent/venv/Scripts/python.exe",
      "args": ["-m", "hermes_cu", "serve"],
      "env": {
        "PYTHONPATH": "C:/path/to/hermes_cu",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

> ⚠️ **Windows spawn note**: If your MCP client uses Node.js `spawn(shell=false)`
> (common in Electron-based tools), you **must** use `python.exe` directly.
> `.cmd` / `.bat` wrappers cause `EINVAL` with `shell:false` on Windows.
> See [CHANGELOG.md](CHANGELOG.md) for details.

Hermes will see 17 tools prefixed `mcp__hermes_cu__*`.

## Recommended agent loop

```text
1.  list_windows                          # what's open?
2.  focus_window "MyApp"                  # bring the target forward
3.  screen_snapshot compact=True          # perceive (text + marks index)
4.  click_mark 5                          # act by mark id (or fall back to click x y)
5.  screen_snapshot compact=True          # verify
6.  if needed: wait_for_text "Saved"      # wait for async UI
```

## Architecture

```
┌──────────────────────┐  JSON-RPC over stdio   ┌──────────────────────┐
│  Hermes (LLM, no     │ ────────────────────►  │   hermes_cu server   │
│  vision)             │  mcp__hermes_cu__*     │  (FastMCP stdio)     │
└──────────────────────┘                        └──────────┬───────────┘
                                                             │
            ┌────────────────────────────────────────────────┼────────────────────┐
            │                          │                          │                    │
       pywinauto UIA              pyautogui                 mss (PIL)        SafetyGuard
       (perception:               (action: mouse/          (verify: PNG
       element tree)              keyboard/clipboard)        screenshot)
```

## Known limits

- **UWP / Electron / WebView2 windows expose no UIA element tree** on
  Windows 11 24H2+. This is a Microsoft design choice, not a `hermes_cu`
  bug. `find_text` returns `[]` inside Calculator, mspaint, MiniMax Code,
  Edge, Chrome, File Explorer. For those targets: use `screenshot_window`
  (mss-based PNG capture, bypasses GPU acceleration) + a vision-capable
  LLM downstream, or use Playwright MCP for browser DOM automation.
- **`screenshot_window`** (v0.3+) uses `mss` with GPU-acceleration detection
  to fall back to `win32ui.PrintWindow` — this captures content even for
  GPU-accelerated windows where direct GDI screenshots are blank.
  `content_ratio > 0.1` confirms real content; `content_ratio < 0.1` means
  the window content is inaccessible (e.g. WeChat internal render).
- **Browser DOM / text reading**: Edge/Chrome webpages require CDP
  (Chrome DevTools Protocol) for DOM text. `find_text` is UIA-only.
  Use **Playwright MCP** (`mcp__playwright__*`) for full browser automation
  including DOM access, navigation, and cookie-aware page interaction.
  hermes_cu and Playwright MCP are complementary: hermes_cu for desktop
  apps + screenshots, Playwright MCP for browser tabs.
- **Hidden / minimized windows** return `bounds=[0,0,0,0]`.
- **Hidden / minimized windows** return `bounds=[0,0,0,0]`.
- **Active-window checks are best-effort.** A malicious or runaway agent
  could `focus_window` then immediately `click` before anyone notices.
  The blacklist mitigates the *common* case (clicking into the owner's
  active chat / browser).

## License

MIT — see [LICENSE](LICENSE).
