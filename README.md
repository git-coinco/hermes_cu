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

  | Perception | Action | Verify |
  | --- | --- | --- |
  | `list_windows` | `click` / `click_mark` | `screenshot` |
  | `focus_window` | `double_click` / `right_click` | |
  | `screen_snapshot` | `type_text` | |
  | `find_text` | `press_key` | |
  | `wait_for_text` | `hotkey` | |
  | `list_marks` | `drag_to` | |
  | | `scroll` / `move_to` | |

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
  bug. The agent can still see the window title, but `find_text` returns
  `[]` inside e.g. the modern Calculator, mspaint, MiniMax Code, Edge,
  Chrome, File Explorer. For those targets, use a vision-capable
  downstream tool or accept coordinate-only control.
- **Hidden / minimized windows** return `bounds=[0,0,0,0]`.
- **Active-window checks are best-effort.** A malicious or runaway agent
  could `focus_window` then immediately `click` before anyone notices.
  The blacklist mitigates the *common* case (clicking into the owner's
  active chat / browser).

## License

MIT — see [LICENSE](LICENSE).
