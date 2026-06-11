from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from ctypes import wintypes


class MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def _working_area() -> tuple[int, int, int, int]:
    point = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    monitor = ctypes.windll.user32.MonitorFromPoint(point, 2)
    info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
    if monitor and ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom
    return 0, 0, 1280, 720


def show(title: str, message: str, duration_ms: int = 8000) -> None:
    root = tk.Tk()
    root.withdraw()
    window = tk.Toplevel(root)
    window.overrideredirect(True)
    window.attributes("-topmost", True)
    window.configure(bg="#216e39")

    body = tk.Frame(window, bg="#f0fff4", padx=18, pady=14)
    body.pack(padx=1, pady=1, fill="both", expand=True)
    tk.Label(
        body,
        text=title,
        bg="#f0fff4",
        fg="#185c30",
        font=("Microsoft YaHei UI", 11, "bold"),
        anchor="w",
    ).pack(fill="x")
    tk.Label(
        body,
        text=message,
        bg="#f0fff4",
        fg="#2f4f3b",
        font=("Microsoft YaHei UI", 10),
        justify="left",
        anchor="w",
        wraplength=380,
    ).pack(fill="x", pady=(7, 0))

    window.update_idletasks()
    left, top, right, bottom = _working_area()
    width = max(360, window.winfo_reqwidth())
    height = window.winfo_reqheight()
    x = right - width - 20
    y = bottom - height - 20
    window.geometry(f"{width}x{height}+{x}+{y}")
    window.deiconify()
    window.after(duration_ms, root.destroy)
    root.mainloop()


def main() -> int:
    title = sys.argv[1] if len(sys.argv) > 1 else "Customer RAG"
    message = sys.argv[2] if len(sys.argv) > 2 else "任务已完成。"
    show(title, message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
