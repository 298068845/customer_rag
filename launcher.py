from __future__ import annotations

import json
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
import ctypes
from datetime import datetime
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from customer_rag.config import load_config
from customer_rag.llama_server import build_llama_server_plan, is_llama_server_healthy
from customer_rag.logging_config import (
    configure_logging,
    install_exception_hooks,
    log_event,
    log_system_snapshot,
    logs_dir,
)

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
LOG_DIR = logs_dir(ROOT)
STREAMLIT_LOG = LOG_DIR / "streamlit.log"
STREAMLIT_ERR = LOG_DIR / "streamlit.error.log"
TALK_STREAMLIT_LOG = LOG_DIR / "talk-streamlit.log"
TALK_STREAMLIT_ERR = LOG_DIR / "talk-streamlit.error.log"
WECHAT_START = ROOT / "wechatExtension" / "start.ps1"
WECHAT_STOP = ROOT / "wechatExtension" / "stop.ps1"
WECHAT_CONFIG = ROOT / "wechatExtension" / "config.ini"
APP_URL = "http://127.0.0.1:8501"
APP_PORT = 8501
TASK_API_URL = "http://127.0.0.1:8512"
SUBSCRIPTION_JOB_STATE = ROOT / "data" / "index" / "subscription_update_job.json"
RAW_JOB_STATE = ROOT / "data" / "index" / "raw_job_state.json"
NOTIFICATION_LOG = LOG_DIR / "notification.log"
NOTIFICATION_REQUEST = ROOT / "data" / "index" / "notification.request.json"
TALK_APP_URL = "http://127.0.0.1:8502"
TALK_APP_PORT = 8502
APP_ICON_PATH = ROOT / "customer_rag" / "assets" / "app_icon.png"
LOCATOR_MODE_DEFAULT = "uia"
LOCATOR_MODE_LABELS = {
    "uia": "UIA \u5b9a\u4f4d",
    "f8": "F8 \u5b9a\u4f4d",
}
SUBSCRIPTION_COMPLETE_NOTIFY_SECTION = "notify"
SUBSCRIPTION_COMPLETE_NOTIFY_KEY = "subscription_complete"
SINGLE_INSTANCE_MUTEX_NAME = "Local\\CustomerRagLauncher_9E4D52A1"

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

state_lock = threading.Lock()
status_text = "启动中"
wechat_running = False
wechat_busy = False
streamlit_process: subprocess.Popen | None = None
talk_streamlit_process: subprocess.Popen | None = None
llama_server_process: subprocess.Popen | None = None
rag_ready = False
single_instance_mutex_handle: int | None = None


def main() -> None:
    configure_logging(ROOT, component="app")
    install_exception_hooks(ROOT, component="app")
    log_system_snapshot(ROOT)
    log_event("app", "launcher_starting", "launcher starting", project_root=ROOT)
    if not acquire_single_instance():
        log_event("app", "launcher_already_running", "another launcher instance is already running", project_root=ROOT)
        return
    ensure_python()
    refresh_wechat_state()
    icon = pystray.Icon(
        "customer_rag",
        create_icon("starting"),
        "Customer RAG",
        menu=build_menu(),
    )
    threading.Thread(target=start_all, args=(icon,), daemon=True).start()
    threading.Thread(target=monitor_cookie_login_state, args=(icon,), daemon=True).start()
    icon.run()


def acquire_single_instance() -> bool:
    if sys.platform != "win32":
        return True
    global single_instance_mutex_handle
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX_NAME)
    if not handle:
        return True
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(handle)
        return False
    single_instance_mutex_handle = handle
    return True


def build_menu() -> pystray.Menu:
    return pystray.Menu(
        pystray.MenuItem(lambda _: f"状态：{get_status()}", noop, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("打开 RAG 页面", open_app),
        pystray.MenuItem("打开话术 RAG 页面", open_talk_app),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "腾讯文档：等待登录",
            noop,
            enabled=False,
            visible=lambda _: is_waiting_for_cookie(),
        ),
        pystray.MenuItem(
            "我已完成登录",
            confirm_tencent_docs_login,
            visible=lambda _: is_waiting_for_cookie(),
        ),
        pystray.MenuItem(
            "取消本次更新",
            cancel_waiting_subscription_update,
            visible=lambda _: is_waiting_for_cookie(),
        ),
        pystray.Menu.SEPARATOR,
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
        pystray.MenuItem(
            "\u6d4b\u8bd5\u6a21\u5f0f\uff08\u4ec5\u7c98\u8d34\uff0c\u4e0d\u53d1\u9001\uff09",
            toggle_test_mode,
            checked=lambda _: get_test_mode(),
        ),
        pystray.MenuItem(
            "\u8ba2\u9605\u5b8c\u6210\u6c14\u6ce1\u901a\u77e5",
            toggle_subscription_complete_notification,
            checked=lambda _: get_subscription_complete_notification(),
        ),
        pystray.MenuItem("重启 RAG 服务", restart_streamlit),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出并停止全部服务", quit_launcher),
    )


def start_all(icon: pystray.Icon) -> None:
    log_event("app", "services_starting", "starting services", project_root=ROOT)
    set_status(icon, "正在启动 RAG 服务", "starting")
    notify(icon, "正在启动 RAG 服务", "请稍候，服务启动完成前不会打开 RAG 页面。")
    if not preflight_check():
        log_event("app", "preflight_failed", "preflight check failed", level=40, project_root=ROOT, include_system=True)
        set_status(icon, "RAG 服务启动失败", "paused")
        notify(icon, "RAG 服务启动失败", "代码自检未通过，请查看 streamlit.err.log。")
        return
    start_llama_server(icon)
    start_streamlit()
    start_talk_streamlit()
    if not wait_for_streamlit() or not wait_for_talk_streamlit():
        log_event("app", "streamlit_healthcheck_failed", "streamlit healthcheck failed", level=40, project_root=ROOT, include_system=True)
        set_status(icon, "RAG 服务启动失败", "paused")
        notify(icon, "RAG 服务启动失败", "请查看 streamlit.err.log 或 streamlit.log。")
        return
    set_status(icon, "正在启动微信插件", "starting")
    start_wechat_plugin(icon)
    log_event("app", "services_started", "services started", project_root=ROOT)
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
    log_event(
        "app",
        "streamlit_started",
        "streamlit process started",
        project_root=ROOT,
        context={"pid": streamlit_process.pid if streamlit_process else 0, "port": APP_PORT},
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
    log_event(
        "app",
        "talk_streamlit_started",
        "talk streamlit process started",
        project_root=ROOT,
        context={"pid": talk_streamlit_process.pid if talk_streamlit_process else 0, "port": TALK_APP_PORT},
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
        stop_process_tree(streamlit_process.pid)
    streamlit_process = None

    for owner in port_owner_pids(APP_PORT):
        run_powershell(f"Stop-Process -Id {owner} -Force")


def stop_talk_streamlit() -> None:
    global talk_streamlit_process
    if talk_streamlit_process and talk_streamlit_process.poll() is None:
        stop_process_tree(talk_streamlit_process.pid)
    talk_streamlit_process = None

    for owner in port_owner_pids(TALK_APP_PORT):
        run_powershell(f"Stop-Process -Id {owner} -Force")


def start_llama_server(icon: pystray.Icon | None = None) -> None:
    global llama_server_process
    config = load_config(ROOT / "config.yaml")
    plan = build_llama_server_plan(config, ROOT)
    if not plan.enabled:
        log_event("llama", "llama_server_disabled", "llama server not started", project_root=ROOT, context={"reason": plan.reason, "backend": config.llm.backend})
        if config.llm.backend == "llama_cpp_server" and icon:
            notify(icon, "llama.cpp server 未启动", plan.reason)
        return
    if is_llama_server_healthy(config):
        return
    if is_port_listening(config.llm.llama_server_port):
        for owner in port_owner_pids(config.llm.llama_server_port):
            run_powershell(f"Stop-Process -Id {owner} -Force")
        time.sleep(1)
    log_path = LOG_DIR / "llama.log"
    err_path = LOG_DIR / "llama.error.log"
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
    log_event(
        "llama",
        "llama_server_started",
        "llama server process started",
        project_root=ROOT,
        context={"pid": llama_server_process.pid if llama_server_process else 0, "backend": plan.backend},
    )
    if icon and config.llm.backend == "llama_cpp_server":
        notify(icon, "正在启动 llama.cpp server", f"后端：{plan.backend}")
    if config.llm.backend == "llama_cpp_server" and not wait_for_llama_server(config):
        log_event("llama", "llama_server_healthcheck_failed", "llama server healthcheck failed", level=40, project_root=ROOT, include_system=True)
        if icon:
            notify(icon, "llama.cpp server 启动超时", "RAG 页面仍会启动，问答会临时回退到检索结果或 Ollama。")


def stop_llama_server() -> None:
    global llama_server_process
    config = load_config(ROOT / "config.yaml")
    if llama_server_process and llama_server_process.poll() is None:
        stop_process_tree(llama_server_process.pid)
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


def is_waiting_for_cookie() -> bool:
    return read_subscription_job_payload().get("status") == "waiting_cookie"


def read_subscription_job_payload() -> dict:
    if not SUBSCRIPTION_JOB_STATE.exists():
        return {}
    try:
        return json.loads(SUBSCRIPTION_JOB_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def monitor_cookie_login_state(icon: pystray.Icon) -> None:
    was_waiting = False
    notified_completed_job = ""
    while True:
        deliver_notification_request(icon)
        payload = read_subscription_job_payload()
        waiting = payload.get("status") == "waiting_cookie"
        if waiting and not was_waiting:
            notify(
                icon,
                "腾讯文档 Cookie 已失效",
                "请重新登录腾讯文档。完成后可等待自动检测，或在托盘菜单点击“我已完成登录”。",
            )
            icon.update_menu()
        elif was_waiting and not waiting:
            icon.update_menu()
        job_id = str(payload.get("job_id") or "")
        if payload.get("status") == "completed" and payload.get("origin") == "auto" and job_id and job_id != notified_completed_job:
            if get_subscription_complete_notification():
                updated_names = payload.get("updated_names") or []
                seconds = max(0, int(payload.get("duration_seconds") or 0))
                duration = f"{seconds // 60}分{seconds % 60}秒" if seconds >= 60 else f"{seconds}秒"
                result = f"{len(updated_names)} 个订阅已更新完毕" if updated_names else "订阅检查完成，无需更新"
                notify(icon, "订阅更新完成", f"{result}，本次任务耗时 {duration}。")
            notified_completed_job = job_id
        was_waiting = waiting
        time.sleep(2)


def deliver_notification_request(icon: pystray.Icon) -> None:
    if not NOTIFICATION_REQUEST.exists():
        return
    try:
        payload = json.loads(NOTIFICATION_REQUEST.read_text(encoding="utf-8-sig"))
        title = str(payload.get("title") or "Customer RAG 测试通知")
        message = str(payload.get("message") or "Windows 托盘气泡通知已正常显示。")
        NOTIFICATION_REQUEST.unlink(missing_ok=True)
        notify(icon, title, message)
    except (OSError, json.JSONDecodeError):
        NOTIFICATION_REQUEST.unlink(missing_ok=True)


def confirm_tencent_docs_login(icon: pystray.Icon, _: object = None) -> None:
    try:
        with urllib.request.urlopen(f"{TASK_API_URL}/cookie/login/read", timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("status") == "completed":
            notify(icon, "Cookie 获取成功", "后台订阅更新将从失败位置继续。")
        else:
            notify(icon, "尚未获取到 Cookie", payload.get("message") or "请确认腾讯文档已经登录后重试。")
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        notify(icon, "Cookie 获取失败", str(exc))
    icon.update_menu()


def cancel_waiting_subscription_update(icon: pystray.Icon, _: object = None) -> None:
    try:
        urllib.request.urlopen(f"{TASK_API_URL}/subscription/stop", timeout=10).read()
        notify(icon, "订阅更新已取消", "本次等待登录的自动更新已停止。")
    except (OSError, urllib.error.URLError) as exc:
        notify(icon, "取消失败", str(exc))
    icon.update_menu()


def start_wechat_plugin(icon: pystray.Icon | None = None, _: object = None) -> None:
    set_wechat_busy(True)
    try:
        if WECHAT_START.exists():
            run_powershell(f"& {quote_ps(WECHAT_START)}")
        set_wechat_running(True)
        log_event("wechat", "wechat_plugin_started", "wechat plugin started", project_root=ROOT)
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
        log_event("wechat", "wechat_plugin_stopped", "wechat plugin stopped", project_root=ROOT)
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


def toggle_test_mode(icon: pystray.Icon, _: object = None) -> None:
    enabled = not get_test_mode()
    write_ini_value(WECHAT_CONFIG, "send", "test_mode", "1" if enabled else "0")
    icon.update_menu()
    message = (
        "\u5df2\u5f00\u542f\uff1a\u53ea\u7c98\u8d34\u5230\u53d1\u9001\u6846\uff0c\u4e0d\u6309 Enter"
        if enabled
        else "\u5df2\u5173\u95ed\uff1a\u6062\u590d\u6b63\u5e38\u53d1\u9001"
    )
    notify(icon, "\u6d4b\u8bd5\u6a21\u5f0f", message)


def toggle_subscription_complete_notification(icon: pystray.Icon, _: object = None) -> None:
    enabled = not get_subscription_complete_notification()
    write_ini_value(
        WECHAT_CONFIG,
        SUBSCRIPTION_COMPLETE_NOTIFY_SECTION,
        SUBSCRIPTION_COMPLETE_NOTIFY_KEY,
        "1" if enabled else "0",
    )
    icon.update_menu()
    message = (
        "\u5df2\u5f00\u542f\uff1a\u81ea\u52a8\u8ba2\u9605\u5b8c\u6210\u540e\u53d1\u9001 Windows \u6c14\u6ce1\u901a\u77e5"
        if enabled
        else "\u5df2\u5173\u95ed\uff1a\u81ea\u52a8\u8ba2\u9605\u5b8c\u6210\u540e\u4e0d\u518d\u53d1\u9001\u6c14\u6ce1\u901a\u77e5"
    )
    notify(icon, "\u8ba2\u9605\u5b8c\u6210\u901a\u77e5", message)


def quit_launcher(icon: pystray.Icon, _: object = None) -> None:
    def worker() -> None:
        stop_background_jobs()
        stop_wechat_plugin()
        stop_llama_server()
        stop_streamlit()
        stop_talk_streamlit()
        icon.visible = False
        icon.stop()

    threading.Thread(target=worker, daemon=True).start()


def stop_background_jobs() -> None:
    try:
        urllib.request.urlopen(f"{TASK_API_URL}/subscription/stop", timeout=3).read()
    except (OSError, urllib.error.URLError):
        pass
    time.sleep(1)
    for state_path in (SUBSCRIPTION_JOB_STATE, RAW_JOB_STATE):
        worker_pid = read_worker_pid(state_path)
        if worker_pid:
            stop_process_tree(worker_pid)


def read_worker_pid(state_path: Path) -> int:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        return max(0, int(payload.get("worker_pid") or 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


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


def stop_process_tree(pid: int | str) -> None:
    try:
        process_id = int(pid)
    except (TypeError, ValueError):
        return
    if process_id <= 0 or process_id == os.getpid():
        return
    subprocess.run(
        ["taskkill", "/PID", str(process_id), "/T", "/F"],
        cwd=ROOT,
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    )


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
    log_event(
        "app",
        "preflight_command_failed",
        "preflight command failed",
        level=40,
        project_root=ROOT,
        context={"returncode": result.returncode, "stderr": result.stderr[-4000:], "stdout": result.stdout[-4000:]},
        include_system=True,
    )
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
        NOTIFICATION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with NOTIFICATION_LOG.open("a", encoding="utf-8") as fp:
            fp.write(f"{datetime.now().isoformat(timespec='seconds')}\t{title}\t{message}\n")
    except OSError:
        pass
    try:
        icon.notify(message, title)
    except Exception:
        pass
    show_desktop_notification(title, message)


def show_desktop_notification(title: str, message: str) -> None:
    pythonw = PYTHON.with_name("pythonw.exe")
    if not pythonw.exists():
        return
    try:
        subprocess.Popen(
            [str(pythonw), "-m", "customer_rag.desktop_notification", title, message],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError:
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


def get_test_mode() -> bool:
    value = read_ini_value(WECHAT_CONFIG, "send", "test_mode", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def get_subscription_complete_notification() -> bool:
    value = read_ini_value(
        WECHAT_CONFIG,
        SUBSCRIPTION_COMPLETE_NOTIFY_SECTION,
        SUBSCRIPTION_COMPLETE_NOTIFY_KEY,
        "1",
    ).strip().lower()
    return value in {"1", "true", "yes", "on"}


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
    try:
        with Image.open(APP_ICON_PATH) as source:
            image = source.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
    except Exception:
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 56, 56), radius=14, fill=(66, 133, 244, 255))
        draw.text((21, 18), "R", fill=(255, 255, 255, 255))
        return image

    draw = ImageDraw.Draw(image)
    if kind == "paused":
        fill = (230, 126, 34, 255)
    elif kind == "running":
        fill = (36, 150, 89, 255)
    else:
        fill = (66, 133, 244, 255)
    draw.ellipse((43, 43, 61, 61), fill=fill, outline=(255, 255, 255, 230), width=2)
    return image


def noop(*_: object) -> None:
    return


if __name__ == "__main__":
    main()
