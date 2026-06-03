from __future__ import annotations

import json
import base64
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


DownloadProgressCallback = Callable[[int, int | None], None]


@dataclass(frozen=True)
class TencentDocSubscription:
    name: str
    url: str
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    last_updated: str = ""
    last_status: str = ""
    last_modified: str = ""


DEFAULT_SUBSCRIPTIONS = [
    ("喜临门京东&迷住专属清单-26年618", "https://docs.qq.com/sheet/DVkh2aG9nbm52bGVI"),
    ("喜临门天猫&迷住专属清单-26年618", "https://docs.qq.com/sheet/DVmRDSE1odnFBUHpR"),
    ("金蝉&迷住专属清单-26年618", "https://docs.qq.com/sheet/DVk50U3NjUG5VTXRM"),
    ("全友&迷住专属清单-26年618 6.1之后", "https://docs.qq.com/sheet/DVm1mU0FpQnhGTVVt"),
    ("迷住&全友清仓清单-26年618-5.11以后", "https://docs.qq.com/sheet/DVnVzTW9HWmR4U0FP"),
    ("出乎&迷住专属清单5.31之后-26年618", "https://docs.qq.com/sheet/DVkdLdm5BaVhyYk1I"),
    ("雅兰天猫&迷住专属清单-26年618（5.13-16）", "https://docs.qq.com/sheet/DVkZYc25CWkZKR1dv"),
    ("蓝盒子天猫&迷住专属清单-26年618", "https://docs.qq.com/sheet/DVnBVYnBKaG1wUFNo"),
    ("窗帘&家纺&地毯&装饰画&迷住专属清单-26年618", "https://docs.qq.com/sheet/DVmFhSGtVRm1naG5k"),
    ("罗莱&迷住专属清单-26年618", "https://docs.qq.com/sheet/DVnFRSWJoYmJqanJD"),
    ("半日闲京东&迷住专属清单-26年618", "https://docs.qq.com/sheet/DVkJoWXJyQXZHZG11"),
    ("半日闲天猫&迷住专属清单-26年618", "https://docs.qq.com/sheet/DVmhjdnpocEFLVW9L"),
    ("乐至宝&迷住专属清单-26年618 5.13之后", "https://docs.qq.com/sheet/DVnVDV2d6T2dXU1JG"),
    ("大自然&林芃&舒达&麻大师&迷住专属清单-26年618", "https://docs.qq.com/sheet/DVk5UT3RKQ3J2RkVt"),
    ("栖作天猫&迷住专属清单-26年618", "https://docs.qq.com/sheet/DVmxKVVRSS1BzV0Zv"),
    ("菠萝斑马天猫&迷住专属清单-26年618", "https://docs.qq.com/sheet/DVkNvRXFsTmdBWWJI"),
    ("帕沙曼&支吾&迷住专属清单5.13日后-26年618", "https://docs.qq.com/sheet/DVlVHSVlqbWpBcU1J"),
    ("芝华仕&迷住专属清单5.13日后-26年618", "https://docs.qq.com/sheet/DVmNWVHB6bW9EYXBi"),
    ("顾家&迷住专属清单-26年618 5.13之后", "https://docs.qq.com/sheet/DVnBBTkJwSGFha0pq"),
    ("源氏木语&迷住专属清单-26年618 5.28", "https://docs.qq.com/sheet/DVkFoZGNNb0pVTHVn"),
    ("迷住&浪度专属清单521-527-26年618", "https://docs.qq.com/sheet/DVm9FT0twcFVwa3pF"),
    ("OOU&迷住专属清单-26年618", "https://docs.qq.com/sheet/DVnFZdGF5YnFCVndq"),
]


def default_subscriptions() -> list[TencentDocSubscription]:
    return [
        TencentDocSubscription(name=name, url=url, tags=["家具"], enabled=True)
        for name, url in DEFAULT_SUBSCRIPTIONS
    ]


def load_subscriptions(path: Path) -> list[TencentDocSubscription]:
    if not path.exists():
        subscriptions = default_subscriptions()
        save_subscriptions(path, subscriptions)
        return subscriptions
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return default_subscriptions()
    subscriptions = []
    for item in payload:
        subscriptions.append(
            TencentDocSubscription(
                name=str(item.get("name", "")).strip(),
                url=str(item.get("url", "")).strip(),
                tags=_clean_tags(item.get("tags", [])),
                enabled=bool(item.get("enabled", True)),
                last_updated=str(item.get("last_updated", "")),
                last_status=str(item.get("last_status", "")),
                last_modified=str(item.get("last_modified", "")),
            )
        )
    return [subscription for subscription in subscriptions if subscription.name and subscription.url]


def save_subscriptions(path: Path, subscriptions: list[TencentDocSubscription]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(item) for item in subscriptions], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def download_subscription(
    subscription: TencentDocSubscription,
    raw_data_dir: Path,
    cookie: str = "",
    progress_callback: DownloadProgressCallback | None = None,
) -> Path:
    doc_id = _extract_doc_id(subscription.url)
    raw_dir = raw_data_dir / "tencent_docs"
    raw_dir.mkdir(parents=True, exist_ok=True)
    output_path = raw_dir / f"{_safe_filename(subscription.name)}.xlsx"

    opener = _build_opener(cookie)
    html = _request_text(opener, subscription.url)
    basic_vars = _read_basic_client_vars(html)
    user_info = basic_vars.get("userInfo", {}) if isinstance(basic_vars.get("userInfo"), dict) else {}
    doc_info = basic_vars.get("docInfo", {}) if isinstance(basic_vars.get("docInfo"), dict) else {}
    pad_info = doc_info.get("padInfo", {}) if isinstance(doc_info.get("padInfo"), dict) else {}

    user_index = str(
        user_info.get("uid")
        or user_info.get("userId")
        or user_info.get("uin")
        or ""
    )
    domain_id = str(pad_info.get("domainId") or doc_info.get("domainId") or "").strip()
    pad_id = str(pad_info.get("localPadId") or pad_info.get("padId") or "").strip()
    local_pad_id = str(
        pad_info.get("globalPadId")
        or doc_info.get("globalPadId")
        or (f"{domain_id}${pad_id}" if domain_id and pad_id else "")
        or pad_id
    )

    user_index = user_index or _find_first(
        html,
        [
            r'"nowUserIndex"\s*:\s*"([^"]+)"',
            r"nowUserIndex\s*=\s*['\"]([^'\"]+)['\"]",
        ],
    )
    local_pad_id = local_pad_id or _find_first(
        html,
        [
            r'"localPadId"\s*:\s*"([^"]+)"',
            r'"padId"\s*:\s*"([^"]+)"',
            r'"docId"\s*:\s*"([^"]+)"',
        ],
    )
    if not user_index or not local_pad_id:
        raise RuntimeError(
            f"无法读取腾讯文档导出参数：{subscription.name}。"
            "请确认文档可公开访问，或在订阅更新时填写已登录 docs.qq.com 的 Cookie。"
        )

    operation_id = _start_export(opener, user_index, local_pad_id)
    file_url = _wait_export(opener, user_index, operation_id)
    data = _request_bytes(opener, file_url, progress_callback=progress_callback)
    if not data:
        raise RuntimeError(f"腾讯文档导出为空：{subscription.name}")
    output_path.write_bytes(data)
    return output_path


def subscription_output_path(subscription: TencentDocSubscription, raw_data_dir: Path) -> Path:
    return raw_data_dir / "tencent_docs" / f"{_safe_filename(subscription.name)}.xlsx"


def fetch_subscription_last_modified(
    subscription: TencentDocSubscription,
    cookie: str = "",
) -> str:
    opener = _build_opener(cookie)
    html = _request_text(opener, subscription.url)
    basic_vars = _read_basic_client_vars(html)
    doc_info = basic_vars.get("docInfo", {}) if isinstance(basic_vars.get("docInfo"), dict) else {}
    value = doc_info.get("lastModifyTime") or doc_info.get("lastModifyTimeMs") or ""
    if not value:
        value = _find_first(html, [r'"lastModifyTime"\s*:\s*(\d+)', r'"last_modify_time"\s*:\s*(\d+)'])
    return _format_remote_time(value)


def update_subscription_status(
    subscriptions: list[TencentDocSubscription],
    target_url: str,
    status: str,
    last_modified: str | None = None,
) -> list[TencentDocSubscription]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    updated = []
    for subscription in subscriptions:
        if subscription.url == target_url:
            updated.append(
                TencentDocSubscription(
                    name=subscription.name,
                    url=subscription.url,
                    tags=subscription.tags,
                    enabled=subscription.enabled,
                    last_updated=now,
                    last_status=status,
                    last_modified=subscription.last_modified if last_modified is None else last_modified,
                )
            )
        else:
            updated.append(subscription)
    return updated


def _extract_doc_id(url: str) -> str:
    match = re.search(r"/(?:sheet|doc|slide)/([^/?#]+)", url)
    if not match:
        raise RuntimeError(f"无法识别腾讯文档地址：{url}")
    return match.group(1)


def _start_export(opener: urllib.request.OpenerDirector, user_index: str, doc_id: str) -> str:
    endpoint = f"https://docs.qq.com/v1/export/export_office?u={urllib.parse.quote(user_index)}"
    body = urllib.parse.urlencode(
        {
            "docId": doc_id,
            "version": "2",
            "exportSource": "client",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://docs.qq.com/",
        },
        method="POST",
    )
    payload = json.loads(_open(opener, request).decode("utf-8"))
    operation_id = str(payload.get("operationId") or payload.get("operation_id") or "")
    if not operation_id:
        raise RuntimeError(f"腾讯文档开始导出失败：{payload}")
    return operation_id


def _wait_export(opener: urllib.request.OpenerDirector, user_index: str, operation_id: str) -> str:
    endpoint = (
        "https://docs.qq.com/v1/export/query_progress?"
        + urllib.parse.urlencode({"u": user_index, "operationId": operation_id})
    )
    for _ in range(60):
        payload = json.loads(_request_text(opener, endpoint))
        file_url = str(payload.get("file_url") or payload.get("fileUrl") or "")
        if file_url:
            return file_url
        if payload.get("ret") not in (0, "0", None):
            raise RuntimeError(f"腾讯文档导出失败：{payload}")
        time.sleep(1)
    raise RuntimeError("腾讯文档导出超时，请稍后重试。")


def _build_opener(cookie: str = "") -> urllib.request.OpenerDirector:
    headers = [
        ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"),
        ("Accept", "*/*"),
    ]
    if cookie.strip():
        headers.append(("Cookie", cookie.strip()))
    opener = urllib.request.build_opener()
    opener.addheaders = headers
    return opener


def _request_text(opener: urllib.request.OpenerDirector, url: str) -> str:
    return _request_bytes(opener, url).decode("utf-8", errors="ignore")


def _request_bytes(
    opener: urllib.request.OpenerDirector,
    url: str,
    progress_callback: DownloadProgressCallback | None = None,
) -> bytes:
    request = urllib.request.Request(url, headers={"Referer": "https://docs.qq.com/"})
    return _open(opener, request, progress_callback=progress_callback)


def _open(
    opener: urllib.request.OpenerDirector,
    request: urllib.request.Request | str,
    progress_callback: DownloadProgressCallback | None = None,
) -> bytes:
    try:
        with opener.open(request, timeout=60) as response:
            if progress_callback is None:
                return response.read()
            total_header = response.headers.get("Content-Length")
            total = int(total_header) if total_header and total_header.isdigit() else None
            chunks: list[bytes] = []
            downloaded = 0
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                chunks.append(chunk)
                downloaded += len(chunk)
                progress_callback(downloaded, total)
            return b"".join(chunks)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"腾讯文档请求失败：HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"腾讯文档请求失败：{exc.reason}") from exc


def _find_first(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def _read_basic_client_vars(html: str) -> dict:
    match = re.search(
        r"window\.basicClientVars=JSON\.parse\(decodeURIComponent\(escape\(atob\('([^']+)'\)\)\)\)",
        html,
    )
    if not match:
        return {}
    try:
        decoded = base64.b64decode(match.group(1)).decode("utf-8", errors="ignore")
        payload = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_filename(name: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    return value[:120] or "腾讯文档订阅"


def _format_remote_time(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        number = int(float(text))
    except ValueError:
        return text
    timestamp = number / 1000 if number > 10_000_000_000 else number
    try:
        return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return text


def _clean_tags(tags: object) -> list[str]:
    if isinstance(tags, str):
        parts = re.split(r"[,，;；|、\s]+", tags)
    elif isinstance(tags, list):
        parts = [str(tag) for tag in tags]
    else:
        parts = []
    cleaned = []
    for part in parts:
        value = part.strip()
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned
