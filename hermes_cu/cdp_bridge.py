"""CDP Bridge — Chrome DevTools Protocol client for Chromium browsers.

Works for: Microsoft Edge, Chrome, Brave, any Chromium-based browser.

Launches the browser with --remote-debugging-port, connects via WebSocket,
and provides:
  - screenshot(): captures the real rendered page (not blank GPU buffer)
  - get_dom_text(): reads all visible DOM text from the page
  - execute_js(): runs arbitrary JS in the page context

Unlike hermes_cu's UIA/Win32 backends, CDP talks directly to the Chromium
renderer process, bypassing GPU compositing and accessibility API limitations.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
import base64
import threading
import subprocess
import os
import sys
from typing import Optional
from dataclasses import dataclass

try:
    import websockets
except ImportError:
    websockets = None


@dataclass
class CDPResult:
    ok: bool
    data: Optional[dict] = None
    error: Optional[str] = None


class CDPBridge:
    """Connect to a Chromium browser's CDP debug port and drive it."""

    def __init__(self, debug_port: int = 9222, browser_exe: Optional[str] = None):
        """
        Args:
            debug_port: Remote debugging port (default 9222).
            browser_exe: Path to browser executable.
                        Auto-detected if not provided.
        """
        self.debug_port = debug_port
        self.browser_exe = browser_exe or self._detect_browser()
        self.ws_url: Optional[str] = None
        self._ws = None
        self._recv_thread: Optional[threading.Thread] = None
        self._pending: dict[str, asyncio.Future] = {}
        self._msg_queue: list[dict] = []
        self._queue_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._process: Optional[subprocess.Popen] = None
        self._tab_id: Optional[str] = None

    def _detect_browser(self) -> str:
        """Detect installed Chromium browser."""
        candidates = [
            # Microsoft Edge
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            # Google Chrome
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            # Brave
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            # Local debug builds
            os.path.expanduser(r"~\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe"),
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        # Fallback: try msedge from PATH
        return "msedge.exe"

    def launch(self, profile_dir: Optional[str] = None, user_data_dir: Optional[str] = None) -> CDPResult:
        """
        Launch browser with remote debugging enabled.
        Returns CDPResult with 'url' on success.
        """
        if websockets is None:
            return CDPResult(ok=False, error="websockets package not installed")

        # Check if already running with debug port
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://localhost:{self.debug_port}/json", timeout=2) as r:
                tabs = json.loads(r.read())
                if tabs:
                    self._tab_id = tabs[0]["id"]
                    self.ws_url = tabs[0]["webSocketDebuggerUrl"]
                    return CDPResult(ok=True, data={"url": tabs[0]["url"], "tabs": len(tabs)})
        except Exception:
            pass

        # Launch new browser
        cmd = [
            self.browser_exe,
            f"--remote-debugging-port={self.debug_port}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if profile_dir:
            cmd.append(f"--profile-directory={profile_dir}")
        if user_data_dir:
            cmd.append(f"--user-data-dir={user_data_dir}")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
            )
        except Exception as e:
            return CDPResult(ok=False, error=f"Failed to launch browser: {e}")

        # Wait for debug port to be ready
        for _ in range(20):
            time.sleep(0.5)
            try:
                import urllib.request
                with urllib.request.urlopen(f"http://localhost:{self.debug_port}/json", timeout=2) as r:
                    tabs = json.loads(r.read())
                    if tabs:
                        self._tab_id = tabs[0]["id"]
                        self.ws_url = tabs[0]["webSocketDebuggerUrl"]
                        return CDPResult(ok=True, data={"url": tabs[0]["url"], "tabs": len(tabs)})
            except Exception:
                continue

        return CDPResult(ok=False, error="Browser launched but debug port did not respond")

    def connect(self, ws_url: Optional[str] = None) -> CDPResult:
        """Connect to the CDP WebSocket. Usually called after launch()."""
        target = ws_url or self.ws_url
        if not target:
            return CDPResult(ok=False, error="No WebSocket URL. Call launch() first or pass ws_url.")

        if websockets is None:
            return CDPResult(ok=False, error="websockets package not installed")

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def _connect():
            self._ws = await websockets.connect(target, max_size=50 * 1024 * 1024)
            # Start recv loop in background
            self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
            self._recv_thread.start()
            return CDPResult(ok=True)

        return self._loop.run_until_complete(_connect())

    def _recv_loop(self):
        """Background thread: recv CDP messages and fill queue."""
        try:
            loop = self._loop
            while True:
                msg = loop.run_until_complete(self._ws.recv())
                data = json.loads(msg)
                with self._queue_lock:
                    self._msg_queue.append(data)
                # Resolve pending futures
                msg_id = data.get("id")
                if msg_id and msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if "result" in data:
                        fut.set_result(data["result"])
                    elif "error" in data:
                        fut.set_exception(Exception(str(data["error"])))
        except Exception:
            pass

    async def _send(self, method: str, params: Optional[dict] = None, tab_id: Optional[str] = None) -> dict:
        """Send CDP command and wait for response."""
        if self._ws is None:
            raise RuntimeError("Not connected. Call connect() first.")

        msg_id = str(uuid.uuid4())
        payload = {
            "id": msg_id,
            "method": method,
            "params": params or {},
        }
        if tab_id:
            payload["sessionId"] = tab_id

        fut: asyncio.Future = asyncio.Future()
        with self._queue_lock:
            self._pending[msg_id] = fut

        await self._ws.send(json.dumps(payload))

        # Wait with timeout
        try:
            return await asyncio.wait_for(fut, timeout=15)
        except asyncio.TimeoutError:
            with self._queue_lock:
                self._pending.pop(msg_id, None)
            raise TimeoutError(f"CDP {method} timed out after 15s")

    def send(self, method: str, params: Optional[dict] = None) -> CDPResult:
        """Synchronous wrapper around _send."""
        if self._loop is None:
            return CDPResult(ok=False, error="Not connected. Call connect() first.")
        try:
            result = self._loop.run_until_complete(self._send(method, params))
            return CDPResult(ok=True, data=result)
        except Exception as e:
            return CDPResult(ok=False, error=str(e))

    def screenshot(self, path: str, format: str = "png", quality: int = 80) -> CDPResult:
        """
        Capture a screenshot of the current page via CDP.
        This captures the GPU-rendered output — NOT a blank buffer.

        Args:
            path: Output file path
            format: 'png' or 'jpeg'
            quality: JPEG quality 0-100

        Returns CDPResult with 'path' on success.
        """
        if not self._tab_id:
            return CDPResult(ok=False, error="No tab target. Call launch() to get a tab.")

        # Activate the tab first
        self.send("Target.activateTarget", {"targetId": self._tab_id})

        result = self.send("Page.captureScreenshot", {
            "format": format,
            "quality": quality,
            "fromSurface": True,
        })

        if not result.ok:
            return result

        try:
            img_data = base64.b64decode(result.data["data"])
            with open(path, "wb") as f:
                f.write(img_data)
            return CDPResult(ok=True, data={"path": path})
        except Exception as e:
            return CDPResult(ok=False, error=f"screenshot decode failed: {e}")

    def get_dom_text(self, max_depth: int = 3) -> CDPResult:
        """
        Read all visible text from the DOM via CDP.
        Uses Runtime.evaluate to run JS that extracts textContent.

        Returns CDPResult with 'text' (full text) and 'elements' (count).
        """
        if not self._tab_id:
            return CDPResult(ok=False, error="No tab target. Call launch() first.")

        # Activate tab
        self.send("Target.activateTarget", {"targetId": self._tab_id})

        js = f"""
        (function() {{
            var texts = [];
            var walker = document.createTreeWalker(
                document.body,
                NodeFilter.SHOW_TEXT,
                {{
                    acceptNode: function(node) {{
                        var txt = node.textContent.trim();
                        if (!txt) return NodeFilter.FILTER_REJECT;
                        if (node.parentElement &&
                            (node.parentElement.tagName === 'SCRIPT' ||
                             node.parentElement.tagName === 'STYLE' ||
                             node.parentElement.tagName === 'NOSCRIPT')) {{
                            return NodeFilter.FILTER_REJECT;
                        }}
                        return NodeFilter.FILTER_ACCEPT;
                    }}
                }}
            );
            var depth = 0;
            while (walker.nextNode() && depth < {max_depth * 1000}) {{
                texts.push(walker.currentNode.textContent.trim());
                depth++;
            }}
            return texts.join('\\n');
        }})()
        """

        result = self.send("Runtime.evaluate", {
            "expression": js,
            "returnByValue": True,
            "generateWebSocketId": True,
        })

        if not result.ok:
            return result

        try:
            text = result.data.get("result", {}).get("value", "")
            elements = len(text.split('\n')) if text else 0
            return CDPResult(ok=True, data={"text": text, "elements": elements})
        except Exception as e:
            return CDPResult(ok=False, error=str(e))

    def get_snapshot(self, max_elements: int = 200) -> CDPResult:
        """
        Get a DOM snapshot using JS traversal — returns structured element list.

        Returns CDPResult with 'elements' (list of {tag, text, role, rect}).
        """
        if not self._tab_id:
            return CDPResult(ok=False, error="No tab target.")

        self.send("Target.activateTarget", {"targetId": self._tab_id})

        js = """
        (function() {
            var results = [];
            var count = 0;
            var walker = document.createTreeWalker(
                document.body,
                NodeFilter.SHOW_ELEMENT,
                {
                    acceptNode: function(node) {
                        if (count >= 200) return NodeFilter.FILTER_REJECT;
                        var tag = node.tagName || '';
                        var text = (node.innerText || '').trim().substring(0, 80);
                        var role = node.getAttribute('role') || '';
                        var placeholder = node.getAttribute('placeholder') || '';
                        var textContent = (node.textContent || '').trim().substring(0, 50);
                        var rect = {};
                        try { var r = node.getBoundingClientRect();
                              if (r.width > 0 && r.height > 0)
                                  rect = {x: Math.round(r.x), y: Math.round(r.y),
                                           w: Math.round(r.width), h: Math.round(r.height)}; } catch(e) {}
                        if (tag && (text || role || placeholder || textContent)) {
                            results.push({tag: tag.toLowerCase(), text: text || textContent,
                                          role: role, placeholder: placeholder, rect: rect});
                            count++;
                        }
                        return NodeFilter.FILTER_SKIP;
                    }
                }
            );
            while (walker.nextNode()) {}
            return results;
        })()
        """
        result = self.send("Runtime.evaluate", {
            "expression": js,
            "returnByValue": True,
        })
        if not result.ok:
            return result
        try:
            elements = result.data.get("result", {}).get("value", [])
            return CDPResult(ok=True, data={"elements": elements, "total": len(elements)})
        except Exception as e:
            return CDPResult(ok=False, error=str(e))

    def click_element(self, selector: str) -> CDPResult:
        """Click an element by CSS selector via JS."""
        if not self._tab_id:
            return CDPResult(ok=False, error="No tab target.")

        self.send("Target.activateTarget", {"targetId": self._tab_id})

        escaped = selector.replace("'", "\\'")
        js = """
        (function() {
            var el = document.querySelector('%s');
            if (!el) return { error: 'not found' };
            el.click();
            return { ok: true, tag: el.tagName };
        })()
        """ % escaped
        result = self.send("Runtime.evaluate", {
            "expression": js,
            "returnByValue": True,
        })
        if not result.ok:
            return result
        try:
            val = result.data.get("result", {}).get("value", {})
            if isinstance(val, dict) and val.get("error"):
                return CDPResult(ok=False, error=val["error"])
            return CDPResult(ok=True, data=val)
        except Exception as e:
            return CDPResult(ok=False, error=str(e))

    def type_text(self, selector: str, text: str) -> CDPResult:
        """Type text into an element by CSS selector."""
        if not self._tab_id:
            return CDPResult(ok=False, error="No tab target.")

        self.send("Target.activateTarget", {"targetId": self._tab_id})

        escaped = selector.replace("'", "\\'")
        js = """
        (function() {
            var el = document.querySelector('%s');
            if (!el) return { error: 'not found' };
            el.focus();
            el.value = '';
            el.innerText = '';
            el.dispatchEvent(new Event('input', { bubbles: true }));
            return { ok: true };
        })()
        """ % escaped
        r1 = self.send("Runtime.evaluate", {"expression": js, "returnByValue": True})
        if not r1.ok:
            return r1

        # Use Input.dispatchKeyEvents for typing
        for char in text:
            self.send("Input.dispatchKeyEvent", {
                "type": "keyDown" if char != "\n" else "rawKeyDown",
                "text": char,
                "key": char,
            })
            self.send("Input.dispatchKeyEvent", {
                "type": "keyUp" if char != "\n" else "rawKeyUp",
                "text": char,
                "key": char,
            })

        return CDPResult(ok=True, data={"text": text, "length": len(text)})

    def navigate(self, url: str) -> CDPResult:
        """Navigate the tab to a URL."""
        if not self._tab_id:
            return CDPResult(ok=False, error="No tab target.")

        self.send("Target.activateTarget", {"targetId": self._tab_id})
        result = self.send("Page.navigate", {"url": url})
        return result

    def get_tab_id(self) -> Optional[str]:
        return self._tab_id

    def close(self):
        """Close the connection and kill the browser process."""
        if self._ws:
            try:
                self._loop.run_until_complete(self._ws.close())
            except Exception:
                pass
        if self._process:
            self._process.terminate()
            self._process = None
        self._tab_id = None
        self._ws_url = None
