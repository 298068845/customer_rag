from __future__ import annotations

import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
STREAMLIT_LOG = ROOT / "streamlit.log"
STREAMLIT_ERR = ROOT / "streamlit.err.log"
WECHAT_START = ROOT / "wechatExtension" / "start.ps1"
WECHAT_STOP = ROOT / "wechatExtension" / "stop.ps1"
APP_URL = "http://127.0.0.1:8501"
APP_PORT = 8501

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

state_lock = threading.Lock()
status_text = "启动中"
wechat_running = False
wechat_busy = False
streamlit_process: subprocess.Popen | None = None


def main() -> None:
    ensure_python()
    refresh_wechat_state()
    icon = pystray.Icon(
        "customer_rag",
        create_icon("starting"),
        "Customer RAG",
        menu=build_menu(),
    )
    threading.Thread(target=start_all, args=(icon,), daemon=True).start()
    icon.run()


def build_menu() -> pystray.Menu:
    return pystray.Menu(
        pystray.MenuItem(lambda _: f"状态：{get_status()}", noop, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("打开 RAG 页面", open_app),
        pystray.MenuItem(
            lambda _: wechat_menu_label(),
            toggle_wechat_plugin,
            checked=lambda _: get_wechat_running(),
            enabled=lambda _: not get_wechat_busy(),
        ),
        pystray.MenuItem("重启 RAG 服务", restart_streamlit),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出启动器", quit_launcher),
    )


def start_all(icon: pystray.Icon) -> None:
    set_status(icon, "正在启动 RAG 服务", "starting")
    start_streamlit()
    wait_for_streamlit()
    set_status(icon, "正在启动微信插件", "starting")
    start_wechat_plugin(icon)
    set_status(icon, "运行中", "running")


def start_streamlit() -> None:
    global streamlit_process
    if is_port_listening(APP_PORT):
        return
    with STREAMLIT_LOG.open("ab") as stdout, STREAMLIT_ERR.open("ab") as stderr:
        streamlit_process = subprocess.Popen(
            [
                str(PYTHON),
                "-m",
                "streamlit",
                "run",
                "app.py",
                "--server.address",
                "127.0.0.1",
                "--server.port",
                str(APP_PORT),
            ],
            cwd=ROOT,
            stdout=stdout,
            stderr=stderr,
            creationflags=CREATE_NO_WINDOW,
        )


def restart_streamlit(icon: pystray.Icon, _: object = None) -> None:
    def worker() -> None:
        set_status(icon, "正在重启 RAG 服务", "starting")
        stop_streamlit()
        time.sleep(1)
        start_streamlit()
        wait_for_streamlit()
        set_status(icon, current_ready_status(), current_icon_kind())

    threading.Thread(target=worker, daemon=True).start()


def stop_streamlit() -> None:
    global streamlit_process
    if streamlit_process and streamlit_process.poll() is None:
        streamlit_process.terminate()
        try:
            streamlit_process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            streamlit_process.kill()
    streamlit_process = None

    owner = port_owner_pid(APP_PORT)
    if owner:
        run_powershell(f"Stop-Process -Id {owner} -Force")


def wait_for_streamlit(timeout_seconds: int = 30) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if is_port_listening(APP_PORT):
            return
        time.sleep(0.5)


def open_app(*_: object) -> None:
    webbrowser.open(APP_URL)


def start_wechat_plugin(icon: pystray.Icon | None = None, _: object = None) -> None:
    set_wechat_busy(True)
    try:
        if WECHAT_START.exists():
            run_powershell(f"& {quote_ps(WECHAT_START)}")
        set_wechat_running(True)
    finally:
        set_wechat_busy(False)
    if icon:
        set_status(icon, "运行中", "running")


def stop_wechat_plugin(icon: pystray.Icon | None = None, _: object = None) -> None:
    set_wechat_busy(True)
    try:
        if WECHAT_STOP.exists():
            run_powershell(f"& {quote_ps(WECHAT_STOP)}")
        set_wechat_running(False)
    finally:
        set_wechat_busy(False)
    if icon:
        set_status(icon, "RAG 运行中，微信插件已关闭", "paused")


def toggle_wechat_plugin(icon: pystray.Icon, _: object = None) -> None:
    if get_wechat_busy():
        return

    def worker() -> None:
        if get_wechat_running():
            stop_wechat_plugin(icon)
        else:
            start_wechat_plugin(icon)
        icon.update_menu()

    set_wechat_busy(True)
    icon.update_menu()
    threading.Thread(target=worker, daemon=True).start()


def quit_launcher(icon: pystray.Icon, _: object = None) -> None:
    def worker() -> None:
        stop_wechat_plugin()
        icon.visible = False
        icon.stop()

    threading.Thread(target=worker, daemon=True).start()


def refresh_wechat_state() -> None:
    set_wechat_running(is_wechat_plugin_running())


def is_wechat_plugin_running() -> bool:
    command = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -like 'AutoHotkey*' -and $_.CommandLine -like '*WeChatQuickTool.ahk*' } | "
        "Select-Object -First 1 -ExpandProperty ProcessId"
    )
    return bool(run_powershell(command).stdout.strip())


def is_port_listening(port: int) -> bool:
    command = (
        f"Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort {port} -ErrorAction SilentlyContinue | "
        "Where-Object { $_.State -eq 'Listen' } | "
        "Select-Object -First 1 -ExpandProperty LocalPort"
    )
    return bool(run_powershell(command).stdout.strip())


def port_owner_pid(port: int) -> str:
    command = (
        f"Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort {port} -ErrorAction SilentlyContinue | "
        "Where-Object { $_.State -eq 'Listen' } | "
        "Select-Object -First 1 -ExpandProperty OwningProcess"
    )
    return run_powershell(command).stdout.strip()


def run_powershell(command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    )


def quote_ps(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def ensure_python() -> None:
    if not PYTHON.exists():
        raise SystemExit(f"Missing virtualenv Python: {PYTHON}")


def set_status(icon: pystray.Icon, text: str, icon_kind: str) -> None:
    global status_text
    with state_lock:
        status_text = text
    icon.title = f"Customer RAG - {text}"
    icon.icon = create_icon(icon_kind)
    icon.update_menu()


def get_status() -> str:
    with state_lock:
        return status_text


def set_wechat_running(value: bool) -> None:
    global wechat_running
    with state_lock:
        wechat_running = value


def get_wechat_running() -> bool:
    with state_lock:
        return wechat_running


def set_wechat_busy(value: bool) -> None:
    global wechat_busy
    with state_lock:
        wechat_busy = value


def get_wechat_busy() -> bool:
    with state_lock:
        return wechat_busy


def wechat_menu_label() -> str:
    if get_wechat_busy():
        return "微信插件：切换中..."
    if get_wechat_running():
        return "微信插件：已开启（点击关闭）"
    return "微信插件：已关闭（点击开启）"


def current_ready_status() -> str:
    return "运行中" if get_wechat_running() else "RAG 运行中，微信插件已关闭"


def current_icon_kind() -> str:
    return "running" if get_wechat_running() else "paused"


def create_icon(kind: str) -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if kind == "paused":
        fill = (230, 126, 34, 255)
    elif kind == "running":
        fill = (36, 150, 89, 255)
    else:
        fill = (66, 133, 244, 255)
    draw.rounded_rectangle((8, 8, 56, 56), radius=14, fill=fill)
    draw.text((21, 18), "R", fill=(255, 255, 255, 255))
    return image


def noop(*_: object) -> None:
    return


if __name__ == "__main__":
    main()
