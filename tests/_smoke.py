import pyautogui
import mss
from pywinauto import Desktop

print("pyautogui.size:", pyautogui.size())

with mss.mss() as sct:
    img = sct.grab(sct.monitors[1])
    print("mss grab ok, size:", img.size, "bytes:", len(img.bgra))

d = Desktop(backend="uia")
windows = d.windows()
print("Desktop windows:", len(windows))
for i, w in enumerate(windows[:5]):
    try:
        print(f"  win{i}:", repr(w.window_text()[:60]))
    except Exception as e:
        print(f"  win{i}: err", str(e)[:40])

print("SMOKE_PASS")
