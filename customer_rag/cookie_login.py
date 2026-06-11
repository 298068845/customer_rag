from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from customer_rag.browser_cookies import (
    close_tencent_docs_login_window,
    is_tencent_docs_login_window_open,
    open_tencent_docs_login_window,
    read_tencent_docs_cookie_from_login_window,
)
from customer_rag.config import RagConfig


@dataclass
class CookieLoginState:
    status: str = "idle"
    started_at: str = ""
    finished_at: str = ""
    message: str = ""
    count: int = 0
    error: str = ""


_lock = threading.Lock()
_worker: threading.Thread | None = None


def cookie_path(config: RagConfig) -> Path:
    return config.index_dir / "tencent_docs_cookie.dat"


def login_state_path(config: RagConfig) -> Path:
    return config.index_dir / "cookie_login_state.json"


def has_saved_cookie(config: RagConfig) -> bool:
    return bool(load_saved_cookie(config))


def save_cookie(config: RagConfig, cookie: str) -> None:
    data = cookie.encode("utf-8")
    try:
        import win32crypt

        data = win32crypt.CryptProtectData(data, "Customer RAG Tencent Docs", None, None, None, 0)
        payload = b"dpapi:" + data
    except (ImportError, OSError):
        payload = b"plain:" + data
    path = cookie_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    tmp_path.write_bytes(payload)
    os.replace(tmp_path, path)


def load_saved_cookie(config: RagConfig) -> str:
    path = cookie_path(config)
    if not path.exists():
        return ""
    try:
        payload = path.read_bytes()
        if payload.startswith(b"dpapi:"):
            import win32crypt

            return win32crypt.CryptUnprotectData(payload[6:], None, None, None, 0)[1].decode("utf-8")
        if payload.startswith(b"plain:"):
            return payload[6:].decode("utf-8")
    except (ImportError, OSError, UnicodeDecodeError):
        return ""
    return ""


def read_login_state(config: RagConfig) -> CookieLoginState:
    path = login_state_path(config)
    if not path.exists():
        return CookieLoginState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return CookieLoginState(status="error", message="Cookie 状态读取失败")
    defaults = asdict(CookieLoginState())
    defaults.update({key: payload.get(key, value) for key, value in defaults.items()})
    return CookieLoginState(**defaults)


def start_cookie_login(config: RagConfig, timeout_seconds: int = 600, poll_seconds: int = 10) -> CookieLoginState:
    global _worker
    with _lock:
        if _worker and _worker.is_alive():
            return read_login_state(config)
        state = CookieLoginState(status="waiting", started_at=_now(), message="请在腾讯文档页面完成登录，系统将自动获取 Cookie")
        _write_state(config, state)
        open_tencent_docs_login_window()
        _worker = threading.Thread(
            target=_poll_cookie,
            args=(config, state, timeout_seconds, poll_seconds),
            daemon=True,
        )
        _worker.start()
        return state


def open_cookie_login(config: RagConfig) -> CookieLoginState:
    state = CookieLoginState(status="window_open", started_at=_now(), message="腾讯文档登录页已打开，登录后请点击获取 Cookie")
    _write_state(config, state)
    if not is_tencent_docs_login_window_open():
        open_tencent_docs_login_window()
    return state


def capture_cookie(config: RagConfig) -> CookieLoginState:
    state = read_login_state(config)
    try:
        result = read_tencent_docs_cookie_from_login_window()
        save_cookie(config, result.cookie)
        close_tencent_docs_login_window()
        state.status = "completed"
        state.finished_at = _now()
        state.count = result.count
        state.error = ""
        state.message = "腾讯文档登录凭证已保存"
        from customer_rag.task_coordinator import schedule_auto_now_on_start

        schedule_auto_now_on_start(config)
    except RuntimeError as exc:
        state.status = "window_open"
        state.error = str(exc)
        state.message = "尚未读取到有效 Cookie，请完成登录后再次点击获取 Cookie"
    _write_state(config, state)
    return state


def cookie_window_is_open() -> bool:
    return is_tencent_docs_login_window_open()


def _poll_cookie(config: RagConfig, state: CookieLoginState, timeout_seconds: int, poll_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        if read_login_state(config).status == "completed":
            return
        try:
            result = read_tencent_docs_cookie_from_login_window()
            save_cookie(config, result.cookie)
            close_tencent_docs_login_window()
            from customer_rag.task_coordinator import schedule_auto_now_on_start

            schedule_auto_now_on_start(config)
            state.status = "completed"
            state.finished_at = _now()
            state.count = result.count
            state.message = "腾讯文档登录凭证已保存"
            _write_state(config, state)
            return
        except RuntimeError as exc:
            last_error = str(exc)
        time.sleep(max(1, poll_seconds))
    state.status = "error"
    state.finished_at = _now()
    state.error = last_error or "等待腾讯文档登录超时"
    state.message = "Cookie 获取超时，请重新登录"
    _write_state(config, state)


def _write_state(config: RagConfig, state: CookieLoginState) -> None:
    path = login_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    tmp_path.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
