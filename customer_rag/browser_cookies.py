from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BrowserCookieResult:
    cookie: str
    browser: str
    profile: str
    count: int
    skipped: int = 0


DEBUG_PORT = 9339
LOGIN_URL = "https://docs.qq.com/"


def open_tencent_docs_login_window(port: int = DEBUG_PORT) -> None:
    browser_path = _find_browser_executable()
    profile_dir = Path("data") / "browser_profile" / "tencent_docs_login"
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        str(browser_path),
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile_dir.resolve()}",
        "--no-first-run",
        "--no-default-browser-check",
        LOGIN_URL,
    ]
    subprocess.Popen(args)


def read_tencent_docs_cookie_from_login_window(port: int = DEBUG_PORT) -> BrowserCookieResult:
    try:
        cookies = _read_cdp_cookies(port)
    except RuntimeError as exc:
        if not _is_websocket_origin_error(str(exc)):
            raise
        _restart_login_window(port)
        cookies = _read_cdp_cookies(port)
    parts: list[str] = []
    for cookie in cookies:
        domain = str(cookie.get("domain", ""))
        name = str(cookie.get("name", ""))
        value = str(cookie.get("value", ""))
        if _domain_matches_docs(domain) and name and value:
            parts.append(f"{name}={value}")
    if not parts:
        raise RuntimeError("专用登录窗口里还没有 docs.qq.com Cookie，请先完成腾讯文档登录")
    return BrowserCookieResult(
        cookie="; ".join(parts),
        browser="专用登录窗口",
        profile="tencent_docs_login",
        count=len(parts),
    )


def _is_websocket_origin_error(message: str) -> bool:
    return "Handshake status 403" in message or "remote-allow-origins" in message


def _restart_login_window(port: int) -> None:
    _stop_login_window(port)
    open_tencent_docs_login_window(port)
    time.sleep(5)


def _stop_login_window(port: int) -> None:
    profile_marker = "tencent_docs_login"
    port_marker = f"remote-debugging-port={port}"
    command = (
        "$targets = Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.Name -eq 'chrome.exe' -and ($_.CommandLine -like '*{profile_marker}*' "
        f"-or $_.CommandLine -like '*{port_marker}*') }}; "
        "$targets | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)


def read_tencent_docs_cookie() -> BrowserCookieResult:
    errors: list[str] = []
    for browser_name, user_data_dir in _browser_user_data_dirs():
        if not user_data_dir.exists():
            continue
        try:
            master_key = _read_master_key(user_data_dir)
        except RuntimeError as exc:
            errors.append(f"{browser_name}: {exc}")
            continue
        for profile_dir in _profile_dirs(user_data_dir):
            cookie_db = profile_dir / "Network" / "Cookies"
            if not cookie_db.exists():
                continue
            try:
                cookie, count, skipped = _read_cookie_db(cookie_db, master_key)
            except RuntimeError as exc:
                errors.append(f"{browser_name}/{profile_dir.name}: {exc}")
                continue
            if cookie:
                return BrowserCookieResult(
                    cookie=cookie,
                    browser=browser_name,
                    profile=profile_dir.name,
                    count=count,
                    skipped=skipped,
                )
    detail = "；".join(errors[-4:]) if errors else "未找到 Chrome/Edge 的 docs.qq.com Cookie"
    raise RuntimeError(detail)


def _browser_user_data_dirs() -> list[tuple[str, Path]]:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    return [
        ("Chrome", local_app_data / "Google" / "Chrome" / "User Data"),
        ("Edge", local_app_data / "Microsoft" / "Edge" / "User Data"),
    ]


def _find_browser_executable() -> Path:
    candidates = [
        shutil.which("chrome"),
        shutil.which("msedge"),
        os.environ.get("CHROME_PATH"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    raise RuntimeError("未找到 Chrome 或 Edge 浏览器")


def _read_cdp_cookies(port: int) -> list[dict]:
    version_url = f"http://127.0.0.1:{port}/json/version"
    deadline = time.time() + 10
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(version_url, timeout=2) as response:
                version = json.loads(response.read().decode("utf-8"))
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.5)
    else:
        raise RuntimeError(f"专用登录窗口未启动成功：{last_error}")

    ws_url = _find_page_websocket_url(port) or str(version.get("webSocketDebuggerUrl") or "")
    if not ws_url:
        raise RuntimeError("未找到专用登录窗口的数据通道")

    try:
        import websocket

        ws = websocket.create_connection(ws_url, timeout=5, suppress_origin=True)
        try:
            ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
            ws.send(json.dumps({"id": 2, "method": "Network.getCookies", "params": {"urls": [LOGIN_URL]}}))
            while True:
                payload = json.loads(ws.recv())
                if payload.get("id") == 2:
                    if "error" in payload:
                        raise RuntimeError(payload["error"])
                    return payload.get("result", {}).get("cookies", [])
        finally:
            ws.close()
    except Exception as exc:
        raise RuntimeError(f"读取专用登录窗口 Cookie 失败：{exc}") from exc


def _find_page_websocket_url(port: int) -> str:
    target_url = f"http://127.0.0.1:{port}/json"
    with urllib.request.urlopen(target_url, timeout=5) as response:
        targets = json.loads(response.read().decode("utf-8"))
    for target in targets:
        if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
            return str(target["webSocketDebuggerUrl"])
    return ""


def _profile_dirs(user_data_dir: Path) -> list[Path]:
    names = ["Default"]
    names.extend(f"Profile {index}" for index in range(1, 20))
    return [user_data_dir / name for name in names if (user_data_dir / name).exists()]


def _read_master_key(user_data_dir: Path) -> bytes:
    local_state = user_data_dir / "Local State"
    if not local_state.exists():
        raise RuntimeError("未找到 Local State")
    try:
        payload = json.loads(local_state.read_text(encoding="utf-8"))
        encrypted_key = base64.b64decode(payload["os_crypt"]["encrypted_key"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Local State 中没有可用的加密密钥") from exc
    if encrypted_key.startswith(b"DPAPI"):
        encrypted_key = encrypted_key[5:]
    return _dpapi_decrypt(encrypted_key)


def _read_cookie_db(cookie_db: Path, master_key: bytes) -> tuple[str, int, int]:
    copied_db = Path(tempfile.gettempdir()) / f"customer_rag_cookies_{os.getpid()}_{cookie_db.parent.parent.name}.sqlite"
    try:
        shutil.copy2(cookie_db, copied_db)
        rows = _query_cookie_rows(copied_db)
    except OSError:
        try:
            rows = _query_cookie_rows(cookie_db, immutable=True)
        except sqlite3.Error as exc:
            raise RuntimeError("Cookie 数据库正被浏览器占用，请关闭浏览器后重试") from exc
    except sqlite3.Error as exc:
        raise RuntimeError("Cookie 数据库读取失败，请关闭浏览器后重试") from exc
    finally:
        try:
            copied_db.unlink(missing_ok=True)
        except OSError:
            pass

    parts: list[str] = []
    skipped = 0
    for host_key, name, value, encrypted_value in rows:
        if not _domain_matches_docs(str(host_key)):
            continue
        cookie_value = str(value or "")
        if not cookie_value and encrypted_value:
            try:
                cookie_value = _decrypt_cookie_value(bytes(encrypted_value), master_key)
            except RuntimeError:
                skipped += 1
                continue
        if name and cookie_value:
            parts.append(f"{name}={cookie_value}")
    return "; ".join(parts), len(parts), skipped


def _query_cookie_rows(cookie_db: Path, immutable: bool = False) -> list[tuple[str, str, str, bytes]]:
    if immutable:
        db_uri = cookie_db.resolve().as_posix()
        with sqlite3.connect(f"file:{db_uri}?mode=ro&immutable=1", uri=True) as conn:
            return _select_cookie_rows(conn)
    with sqlite3.connect(cookie_db) as conn:
        return _select_cookie_rows(conn)


def _select_cookie_rows(conn: sqlite3.Connection) -> list[tuple[str, str, str, bytes]]:
    return conn.execute(
        """
        select host_key, name, value, encrypted_value
        from cookies
        where host_key like '%docs.qq.com%' or host_key like '%.qq.com' or host_key = 'qq.com'
        """
    ).fetchall()


def _domain_matches_docs(host_key: str) -> bool:
    host = host_key.lstrip(".").lower()
    return host == "docs.qq.com" or host.endswith(".docs.qq.com")


def _decrypt_cookie_value(encrypted_value: bytes, master_key: bytes) -> str:
    if not encrypted_value:
        return ""
    if encrypted_value.startswith((b"v10", b"v11", b"v20")):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            nonce = encrypted_value[3:15]
            ciphertext = encrypted_value[15:]
            return AESGCM(master_key).decrypt(nonce, ciphertext, None).decode("utf-8")
        except Exception as exc:
            raise RuntimeError("Chrome Cookie AES 解密失败") from exc
    return _dpapi_decrypt(encrypted_value).decode("utf-8")


def _dpapi_decrypt(value: bytes) -> bytes:
    try:
        import win32crypt

        return win32crypt.CryptUnprotectData(value, None, None, None, 0)[1]
    except Exception as exc:
        raise RuntimeError("Windows DPAPI 解密失败，请用当前 Windows 用户运行本程序") from exc
