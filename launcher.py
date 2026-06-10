from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from customer_rag.config import load_config
from customer_rag.llama_server import build_llama_server_plan, is_llama_server_healthy

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
STREAMLIT_LOG = ROOT / "streamlit.log"
STREAMLIT_ERR = ROOT / "streamlit.err.log"
TALK_STREAMLIT_LOG = ROOT / "talk-streamlit.log"
TALK_STREAMLIT_ERR = ROOT / "talk-streamlit.err.log"
WECHAT_START = ROOT / "wechatExtension" / "start.ps1"
WECHAT_STOP = ROOT / "wechatExtension" / "stop.ps1"
WECHAT_CONFIG = ROOT / "wechatExtension" / "config.ini"
APP_URL = "http://127.0.0.1:8501"
APP_PORT = 8501
TALK_APP_URL = "http://127.0.0.1:8502"
TALK_APP_PORT = 8502
LOCATOR_MODE_DEFAULT = "uia"
LOCATOR_MODE_LABELS = {
    "uia": "UIA \u5b9a\u4f4d",
    "f8": "F8 \u5b9a\u4f4d",
}

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

state_lock = threading.Lock()
status_text = "启动中"
wechat_running = False
wechat_busy = False
streamlit_process: subprocess.Popen | None = None
talk_streamlit_process: subprocess.Popen | None = None
llama_server_process: subprocess.Popen | None = None
rag_ready = False


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
        pystray.MenuItem("打开话术 RAG 页面", open_talk_app),
        pystray.MenuItem(
            lambda _: wechat_menu_label(),
            toggle_wechat_plugin,
            checked=lambda _: get_wechat_running(),
            enabled=lambda _: not get_wechat_busy(),
        ),
        pystray.MenuItem(
            "\u5b9a\u4f4d\u6a21\u5f0f",
            pystray.Menu(
                pystray.MenuItem(
                    LOCATOR_MODE_LABELS["uia"],
                    set_locator_mode_uia,
                    checked=lambda _: get_locator_mode() == "uia",
                    radio=True,
                ),
                pystray.MenuItem(
                    LOCATOR_MODE_LABELS["f8"],
                    set_locator_mode_f8,
                    checked=lambda _: get_locator_mode() == "f8",
                    radio=True,
                ),
            ),
        ),
        pystray.MenuItem("重启 RAG 服务", restart_streamlit),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出启动器", quit_launcher),
    )


def start_all(icon: pystray.Icon) -> None:
    set_status(icon, "正在启动 RAG 服务", "starting")
    notify(icon, "正在启动 RAG 服务", "请稍候，服务启动完成前不会打开 RAG 页面。")
    if not preflight_check():
        set_status(icon, "RAG 服务启动失败", "paused")
        notify(icon, "RAG 服务启动失败", "代码自检未通过，请查看 streamlit.err.log。")
        return
    start_llama_server(icon)
    start_streamlit()
    start_talk_streamlit()
    if not wait_for_streamlit() or not wait_for_talk_streamlit():
        set_status(icon, "RAG 服务启动失败", "paused")
        notify(icon, "RAG 服务启动失败", "请查看 streamlit.err.log 或 streamlit.log。")
        return
    set_status(icon, "正在启动微信插件", "starting")
    start_wechat_plugin(icon)
    set_status(icon, "运行中", "running")
    open_app(icon)


def start_streamlit() -> None:
    global streamlit_process
    if is_streamlit_healthy():
        set_rag_ready(True)
        return
    if is_port_listening(APP_PORT):
        stop_streamlit()
    with STREAMLIT_LOG.open("ab") as stdout, STREAMLIT_ERR.open("ab") as stderr:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["VIRTUAL_ENV"] = str(ROOT / ".venv")
        env["PATH"] = str(ROOT / ".venv" / "Scripts") + os.pathsep + env.get("PATH", "")
        streamlit_process = subprocess.Popen(
            [
                str(PYTHON),
                "run_streamlit.py",
            ],
            cwd=ROOT,
            stdout=stdout,
            stderr=stderr,
            env=env,
            creationflags=CREATE_NO_WINDOW,
        )


def start_talk_streamlit() -> None:
    global talk_streamlit_process
    if is_talk_streamlit_healthy():
        return
    if is_port_listening(TALK_APP_PORT):
        stop_talk_streamlit()
    with TALK_STREAMLIT_LOG.open("ab") as stdout, TALK_STREAMLIT_ERR.open("ab") as stderr:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["VIRTUAL_ENV"] = str(ROOT / ".venv")
        env["PATH"] = str(ROOT / ".venv" / "Scripts") + os.pathsep + env.get("PATH", "")
        talk_streamlit_process = subprocess.Popen(
            [
                str(PYTHON),
                "run_talk_streamlit.py",
            ],
            cwd=ROOT,
            stdout=stdout,
            stderr=stderr,
            env=env,
            creationflags=CREATE_NO_WINDOW,
        )


def restart_streamlit(icon: pystray.Icon, _: object = None) -> None:
    def worker() -> None:
        set_status(icon, "正在重启 RAG 服务", "starting")
        set_rag_ready(False)
        notify(icon, "正在重启 RAG 服务", "请稍候，重启完成前不会打开 RAG 页面。")
        stop_streamlit()
        stop_talk_streamlit()
        stop_llama_server()
        time.sleep(1)
        if not preflight_check():
            set_status(icon, "RAG 服务启动失败", "paused")
            notify(icon, "RAG 服务启动失败", "代码自检未通过，请查看 streamlit.err.log。")
            return
        start_llama_server(icon)
        start_streamlit()
        start_talk_streamlit()
        if wait_for_streamlit() and wait_for_talk_streamlit():
            set_status(icon, current_ready_status(), current_icon_kind())
            notify(icon, "RAG 服务已启动", "现在可以打开 RAG 页面。")
        else:
            set_status(icon, "RAG 服务启动失败", "paused")
            notify(icon, "RAG 服务启动失败", "请查看 streamlit.err.log 或 streamlit.log。")

    threading.Thread(target=worker, daemon=True).start()


def stop_streamlit() -> None:
    global streamlit_process
    set_rag_ready(False)
    if streamlit_process and streamlit_process.poll() is None:
        streamlit_process.terminate()
        try:
            streamlit_process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            streamlit_process.kill()
    streamlit_process = None

    for owner in port_owner_pids(APP_PORT):
        run_powershell(f"Stop-Process -Id {owner} -Force")


def stop_talk_streamlit() -> None:
    global talk_streamlit_process
    if talk_streamlit_process and talk_streamlit_process.poll() is None:
        talk_streamlit_process.terminate()
        try:
            talk_streamlit_process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            talk_streamlit_process.kill()
    talk_streamlit_process = None

    for owner in port_owner_pids(TALK_APP_PORT):
        run_powershell(f"Stop-Process -Id {owner} -Force")


def start_llama_server(icon: pystray.Icon | None = None) -> None:
    global llama_server_process
    config = load_config(ROOT / "config.yaml")
    plan = build_llama_server_plan(config, ROOT)
    if not plan.enabled:
        if config.llm.backend == "llama_cpp_server" and icon:
            notify(icon, "llama.cpp server 未启动", plan.reason)
        return
    if is_llama_server_healthy(config):
        return
    if is_port_listening(config.llm.llama_server_port):
        for owner in port_owner_pids(config.llm.llama_server_port):
            run_powershell(f"Stop-Process -Id {owner} -Force")
        time.sleep(1)
    log_path = ROOT / "llama-server.log"
    err_path = ROOT / "llama-server.err.log"
    with log_path.open("ab") as stdout, err_path.open("ab") as stderr:
        env = dict(os.environ)
        env["LLAMA_ARG_NO_DISPLAY_PROMPT"] = "1"
        llama_server_process = subprocess.Popen(
            plan.args,
            cwd=ROOT,
            stdout=stdout,
            stderr=stderr,
            env=env,
            creationflags=CREATE_NO_WINDOW,
        )
    if icon and config.llm.backend == "llama_cpp_server":
        notify(icon, "正在启动 llama.cpp server", f"后端：{plan.backend}")
    if config.llm.backend == "llama_cpp_server" and not wait_for_llama_server(config):
        if icon:
            notify(icon, "llama.cpp server 启动超时", "RAG 页面仍会启动，问答会临时回退到检索结果或 Ollama。")


def stop_llama_server() -> None:
    global llama_server_process
    config = load_config(ROOT / "config.yaml")
    if llama_server_process and llama_server_process.poll() is None:
        llama_server_process.terminate()
        try:
            llama_server_process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            llama_server_process.kill()
    llama_server_process = None
    for owner in port_owner_pids(config.llm.llama_server_port):
        run_powershell(f"Stop-Process -Id {owner} -Force")


def wait_for_llama_server(config, timeout_seconds: int = 60) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if is_llama_server_healthy(config):
            return True
        if llama_server_process and llama_server_process.poll() is not None:
            return False
        time.sleep(0.75)
    return False


def wait_for_streamlit(timeout_seconds: int = 60) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if is_streamlit_healthy():
            set_rag_ready(True)
            return True
        time.sleep(0.5)
    set_rag_ready(False)
    return False


def wait_for_talk_streamlit(timeout_seconds: int = 60) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if is_talk_streamlit_healthy():
            return True
        time.sleep(0.5)
    return False


def open_app(icon: pystray.Icon | None = None, *_: object) -> None:
    if not get_rag_ready() and not is_streamlit_healthy():
        if icon:
            set_status(icon, "正在启动 RAG 服务", "starting")
            notify(icon, "正在启动 RAG 服务", "服务未启动完成，暂不打开 RAG 页面。")
        return
    set_rag_ready(True)
    webbrowser.open(APP_URL)


def open_talk_app(icon: pystray.Icon | None = None, *_: object) -> None:
    if not is_talk_streamlit_healthy():
        if icon:
            set_status(icon, "正在启动话术 RAG 服务", "starting")
            notify(icon, "正在启动话术 RAG 服务", "服务未启动完成，暂不打开话术 RAG 页面。")
        return
    webbrowser.open(TALK_APP_URL)


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


def set_locator_mode_uia(icon: pystray.Icon, _: object = None) -> None:
    set_locator_mode(icon, "uia")


def set_locator_mode_f8(icon: pystray.Icon, _: object = None) -> None:
    set_locator_mode(icon, "f8")


def set_locator_mode(icon: pystray.Icon, mode: str) -> None:
    mode = normalize_locator_mode(mode)
    write_ini_value(WECHAT_CONFIG, "send", "locator_mode", mode)
    icon.update_menu()
    notify(icon, "\u5b9a\u4f4d\u6a21\u5f0f\u5df2\u5207\u6362", LOCATOR_MODE_LABELS[mode])


def quit_launcher(icon: pystray.Icon, _: object = None) -> None:
    def worker() -> None:
        stop_wechat_plugin()
        stop_llama_server()
        stop_streamlit()
        stop_talk_streamlit()
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
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def port_owner_pids(port: int) -> list[str]:
    result = subprocess.run(
        ["netstat", "-ano"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    )
    owners: list[str] = []
    marker = f":{port}"
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(marker) and parts[3].upper() == "LISTENING":
            owner = parts[4]
            if owner not in owners:
                owners.append(owner)
    return owners


def run_powershell(command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    )


def preflight_check() -> bool:
    script = (
        "from customer_rag.tencent_docs import fetch_subscription_last_modified; "
        "from customer_rag.pipeline import RagPipeline; "
        "from customer_rag.talk_rag import TalkRagEngine; "
        "assert hasattr(RagPipeline, 'replace_files_with_tags'); "
        "assert TalkRagEngine().ask('今日清单是什么').answer"
    )
    result = subprocess.run(
        [str(PYTHON), "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    )
    if result.returncode == 0:
        return True
    with STREAMLIT_ERR.open("ab") as stderr:
        stderr.write(("\n[launcher preflight failed]\n" + result.stderr + result.stdout).encode("utf-8", errors="ignore"))
    return False


def is_streamlit_healthy() -> bool:
    if not is_port_listening(APP_PORT):
        return False
    try:
        with urllib.request.urlopen(APP_URL, timeout=3) as response:
            body = response.read(200_000).decode("utf-8", errors="ignore")
    except (urllib.error.URLError, TimeoutError):
        return False
    error_markers = [
        "Traceback:",
        "ImportError:",
        "ModuleNotFoundError:",
        "AttributeError:",
        "Uncaught app exception",
    ]
    return not any(marker in body for marker in error_markers)


def is_talk_streamlit_healthy() -> bool:
    if not is_port_listening(TALK_APP_PORT):
        return False
    try:
        with urllib.request.urlopen(TALK_APP_URL, timeout=3) as response:
            body = response.read(200_000).decode("utf-8", errors="ignore")
    except (urllib.error.URLError, TimeoutError):
        return False
    error_markers = [
        "Traceback:",
        "ImportError:",
        "ModuleNotFoundError:",
        "AttributeError:",
        "Uncaught app exception",
    ]
    return not any(marker in body for marker in error_markers)


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


def set_rag_ready(value: bool) -> None:
    global rag_ready
    with state_lock:
        rag_ready = value


def get_rag_ready() -> bool:
    with state_lock:
        return rag_ready


def notify(icon: pystray.Icon, title: str, message: str) -> None:
    try:
        icon.notify(message, title)
    except Exception:
        pass


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


def get_locator_mode() -> str:
    return normalize_locator_mode(read_ini_value(WECHAT_CONFIG, "send", "locator_mode", LOCATOR_MODE_DEFAULT))


def normalize_locator_mode(value: str | None) -> str:
    mode = (value or LOCATOR_MODE_DEFAULT).strip().lower()
    if mode in {"f8", "saved_point", "saved-point", "point"}:
        return "f8"
    return "uia"


def read_ini_value(path: Path, section: str, key: str, default: str) -> str:
    if not path.exists():
        return default

    current_section = ""
    section_pattern = re.compile(r"^\s*\[([^\]]+)\]\s*$")
    key_pattern = re.compile(r"^\s*([^=;#]+?)\s*=")
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            section_match = section_pattern.match(line)
            if section_match:
                current_section = section_match.group(1).strip().lower()
                continue
            key_match = key_pattern.match(line)
            if current_section == section.lower() and key_match and key_match.group(1).strip().lower() == key.lower():
                return line.split("=", 1)[1].strip()
    except OSError:
        return default
    return default


def write_ini_value(path: Path, section: str, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines(keepends=True) if path.exists() else []
    section_header = f"[{section}]\n"
    key_line = f"{key}={value}\n"
    section_pattern = re.compile(r"^\s*\[([^\]]+)\]\s*$")
    key_pattern = re.compile(r"^\s*([^=;#]+?)\s*=")
    in_section = False
    section_found = False

    for index, line in enumerate(lines):
        section_match = section_pattern.match(line.strip())
        if section_match:
            if in_section:
                lines.insert(index, key_line)
                path.write_text("".join(lines), encoding="utf-8")
                return
            in_section = section_match.group(1).strip().lower() == section.lower()
            section_found = section_found or in_section
            continue

        key_match = key_pattern.match(line)
        if in_section and key_match and key_match.group(1).strip().lower() == key.lower():
            newline = "\r\n" if line.endswith("\r\n") else "\n"
            lines[index] = f"{key}={value}{newline}"
            path.write_text("".join(lines), encoding="utf-8")
            return

    if section_found:
        lines.append(key_line)
    else:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += "\n"
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.extend([section_header, key_line])
    path.write_text("".join(lines), encoding="utf-8")


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
