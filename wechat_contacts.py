# wechat_contacts.py - Use hermes_cu to open WeChat and count contacts
import subprocess, json, sys, time, os

PYTHON = r"C:\Users\CLL\.hermes\hermes-agent\venv\Scripts\python.exe"
PYTHONPATH = r"D:\Hermes_Backup\github\hermes_cu"
ENV = {**os.environ, "PYTHONPATH": PYTHONPATH, "PYTHONIOENCODING": "utf-8"}

def run_tool(tool_name, args=None):
    """Call hermes_cu MCP tool and return parsed JSON result."""
    proc = subprocess.Popen(
        [PYTHON, "-m", "hermes_cu", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=ENV,
    )

    # Initialize
    init_req = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "0.1.0", "capabilities": {},
                          "clientInfo": {"name": "wechat_test", "version": "1.0"}}}
    proc.stdin.write(json.dumps(init_req).encode() + b"\n")
    proc.stdin.flush()
    time.sleep(0.3)

    # notifications/initialized
    proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}\n')
    proc.stdin.flush()
    time.sleep(0.3)

    # Call tool
    tool_req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": tool_name, "arguments": args or {}}}
    proc.stdin.write(json.dumps(tool_req).encode() + b"\n")
    proc.stdin.flush()
    time.sleep(1.0)
    proc.stdin.close()

    stdout = proc.stdout.read().decode("utf-8", errors="replace")
    stderr = proc.stderr.read().decode("utf-8", errors="replace")
    proc.wait()

    if stderr.strip():
        print(f"STDERR: {stderr[:200]}", file=sys.stderr)

    # Parse JSON-RPC responses
    results = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            results.append(obj)
        except:
            pass

    return results


def main():
    print("=== Step 1: List windows ===")
    results = run_tool("list_windows")
    for r in results:
        if r.get("id") == 1 and "result" in r:
            content = r["result"].get("content", [])
            for block in content:
                text = block.get("text", "")
                if "wechat" in text.lower() or "微信" in text:
                    print("Found WeChat:", text[:200])

    print("\n=== Step 2: Find WeChat window ===")
    results = run_tool("find_text", {"text": "微信", "max_results": 10})
    for r in results:
        if r.get("id") == 1 and "result" in r:
            content = r["result"].get("content", [])
            for block in content:
                print("Find result:", block.get("text", "")[:300])

    print("\n=== Step 3: Screen snapshot (active window) ===")
    results = run_tool("screen_snapshot", {"compact": False, "max_depth": 3})
    for r in results:
        if r.get("id") == 1 and "result" in r:
            content = r["result"].get("content", [])
            for block in content:
                print("Snapshot:", block.get("text", "")[:500])

    print("\nDone.")


if __name__ == "__main__":
    main()
