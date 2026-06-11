from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from customer_rag.config import RagConfig
from customer_rag.cookie_login import load_saved_cookie, read_login_state, start_cookie_login
from customer_rag.process_utils import process_is_alive, start_worker_process
from customer_rag.task_coordinator import read_state as read_coordinator_state, release, try_acquire
from customer_rag.time_format import display_datetime, display_datetimes_in_text, now_display
from customer_rag.tencent_docs import (
    TencentDocSubscription,
    download_subscription,
    fetch_subscription_last_modified,
    fetch_subscription_page,
    load_subscriptions,
    save_subscriptions,
    subscription_output_path,
    update_subscription_status,
)


SUBSCRIPTION_MAX_WORKERS = 4


@dataclass
class SubscriptionJobState:
    job_id: str = ""
    status: str = "idle"
    started_at: str = ""
    finished_at: str = ""
    current_name: str = ""
    current_index: int = 0
    total: int = 0
    current_downloaded: int = 0
    current_total: int | None = None
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    removed: int = 0
    documents: int = 0
    items: int = 0
    chunks: int = 0
    percent: int | None = None
    index_error: str = ""
    message: str = ""
    updated_files: list[str] = field(default_factory=list)
    pending_modified_by_url: dict[str, str] = field(default_factory=dict)
    updated_names: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    origin: str = "manual"
    duration_seconds: int = 0
    cookie_refresh_required: bool = False
    worker_pid: int = 0


_lock = threading.Lock()
_worker: threading.Thread | None = None


def job_state_path(config: RagConfig) -> Path:
    return config.index_dir / "subscription_update_job.json"


def stop_flag_path(config: RagConfig) -> Path:
    return config.index_dir / "subscription_update_job.stop"


def _worker_request_path(config: RagConfig, job_id: str) -> Path:
    return config.index_dir / f"subscription_update_job.{job_id}.request.json"


def read_job_state(config: RagConfig) -> SubscriptionJobState:
    path = job_state_path(config)
    if not path.exists():
        return SubscriptionJobState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SubscriptionJobState(status="error", message="任务状态文件读取失败")
    defaults = asdict(SubscriptionJobState())
    defaults.update({key: payload.get(key, value) for key, value in defaults.items()})
    if defaults["logs"] is None:
        defaults["logs"] = []
    defaults["logs"] = [display_datetimes_in_text(log) for log in defaults["logs"]]
    defaults["message"] = display_datetimes_in_text(defaults["message"])
    defaults["current_name"] = display_datetimes_in_text(defaults["current_name"])
    if defaults["updated_names"] is None:
        defaults["updated_names"] = []
    return SubscriptionJobState(**defaults)


def is_subscription_worker_alive() -> bool:
    state = read_job_state(load_config_for_pid_check())
    return bool(_worker and _worker.is_alive()) or process_is_alive(int(state.worker_pid or 0))


def recover_interrupted_subscription_job(config: RagConfig) -> SubscriptionJobState:
    state = read_job_state(config)
    worker_alive = bool(_worker and _worker.is_alive()) or process_is_alive(int(state.worker_pid or 0))
    if state.status in {"running", "waiting_cookie", "rebuilding", "stopping"} and not worker_alive:
        state.status = "error"
        state.finished_at = _now()
        state.message = "任务已中断：服务重启或后台 worker 已不存在"
        _add_log(state, state.message)
        _write_state(config, state)
    return state


def start_subscription_job(
    config: RagConfig,
    subscriptions_path: Path,
    subscriptions: list[TencentDocSubscription],
    cookie: str,
    *,
    origin: str = "manual",
) -> SubscriptionJobState:
    global _worker
    with _lock:
        state = recover_interrupted_subscription_job(config)
        if state.status in {"running", "waiting_cookie", "rebuilding", "stopping"} and (
            bool(_worker and _worker.is_alive()) or process_is_alive(int(state.worker_pid or 0))
        ):
            return state
        job_id = uuid4().hex
        if not try_acquire(config, f"subscription:{origin}", job_id):
            return SubscriptionJobState(status="busy", message="另一个导入或订阅任务正在运行")
        stop_flag_path(config).unlink(missing_ok=True)
        state = SubscriptionJobState(
            job_id=job_id,
            status="running",
            started_at=_now(),
            total=len(subscriptions),
            percent=0,
            message="订阅下载已启动",
            origin=origin,
        )
        _write_state(config, state)
        if os.environ.get("CUSTOMER_RAG_INLINE_WORKER") == "1":
            _worker = threading.Thread(
                target=_run_subscription_job,
                args=(config, subscriptions_path, subscriptions, cookie, job_id),
                daemon=True,
            )
            _worker.start()
        else:
            try:
                request_path = _worker_request_path(config, job_id)
                request_path.write_text(
                    json.dumps(
                        {
                            "subscriptions_path": str(subscriptions_path),
                            "subscriptions": [asdict(subscription) for subscription in subscriptions],
                            "origin": origin,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                state.worker_pid = start_worker_process(["subscription", job_id, str(request_path), origin], Path.cwd())
                _write_state(config, state)
            except Exception as exc:
                state.status = "error"
                state.finished_at = _now()
                state.message = f"启动独立订阅更新进程失败：{exc}"
                _write_state(config, state)
                release(config, job_id, f"auto:{state.status}" if origin == "auto" else state.status)
        return state


def request_stop_subscription_job(config: RagConfig) -> None:
    stop_flag_path(config).parent.mkdir(parents=True, exist_ok=True)
    stop_flag_path(config).write_text("stop", encoding="utf-8")
    state = read_job_state(config)
    if state.status in {"running", "waiting_cookie"}:
        state.status = "stopping"
        state.message = "正在停止，当前安全点结束后退出"
        _write_state(config, state)


def is_subscription_job_running(config: RagConfig) -> bool:
    global _worker
    state = read_job_state(config)
    return state.status in {"running", "waiting_cookie", "rebuilding", "stopping"} and (
        bool(_worker and _worker.is_alive()) or process_is_alive(int(state.worker_pid or 0))
    )


def _run_subscription_job(
    config: RagConfig,
    subscriptions_path: Path,
    subscriptions: list[TencentDocSubscription],
    cookie: str,
    job_id: str,
) -> None:
    state = read_job_state(config)
    started_monotonic = time.monotonic()
    state_lock = threading.Lock()
    next_subscriptions = subscriptions
    downloaded_paths: list[Path] = []
    failed_subscriptions: list[TencentDocSubscription] = []
    cookie_failed_subscriptions: list[TencentDocSubscription] = []
    active_cookie = cookie

    def current_state_is_active() -> bool:
        return read_job_state(config).job_id == job_id

    def save_status(subscription: TencentDocSubscription, status: str, last_modified: str | None = None) -> None:
        nonlocal next_subscriptions
        next_subscriptions = update_subscription_status(
            next_subscriptions,
            subscription.url,
            status,
            last_modified=last_modified,
        )
        save_subscriptions(subscriptions_path, next_subscriptions)

    def write_state() -> None:
        if current_state_is_active():
            _write_state(config, state)

    def run_one(index: int, subscription: TencentDocSubscription) -> None:
        nonlocal downloaded_paths, active_cookie
        if _stop_requested(config):
            return

        with state_lock:
            state.current_index = index
            state.current_name = subscription.name
            state.current_downloaded = 0
            state.current_total = None
            state.message = f"正在更新：{subscription.name}"
            save_status(subscription, "更新中")
            local_modified = display_datetime(subscription.last_modified) or "空"
            _add_log(state, f"{subscription.name} 开始检查；本地记录最后修改={local_modified}")
            write_state()

        try:
            page = fetch_subscription_page(subscription, active_cookie)
            remote_modified = fetch_subscription_last_modified(subscription, active_cookie, page=page)
            local_modified = display_datetime(subscription.last_modified)
            output_path = subscription_output_path(subscription, config.raw_data_dir)
            local_exists = output_path.exists()

            with state_lock:
                _add_log(
                    state,
                    (
                        f"{subscription.name} 远端最后修改={display_datetime(remote_modified) or '未读取到'}；"
                        f"本地文件={'存在' if local_exists else '不存在'}；路径={output_path}"
                    ),
                )
                write_state()

            if remote_modified and display_datetime(remote_modified) == local_modified and local_exists:
                with state_lock:
                    state.skipped += 1
                    save_status(subscription, "跳过：文件未变化", last_modified=remote_modified)
                    _add_log(
                        state,
                        f"{subscription.name} 跳过：远端最后修改与已入库记录一致 ({display_datetime(remote_modified)})，且本地文件存在",
                    )
                    write_state()
                return

            with state_lock:
                if not remote_modified:
                    _add_log(state, f"{subscription.name} 决定下载：未读取到远端最后修改时间，无法确认本地是否最新")
                elif display_datetime(remote_modified) != local_modified:
                    _add_log(
                        state,
                        f"{subscription.name} 决定下载：远端最后修改 {display_datetime(remote_modified)} != 已入库记录 {local_modified or '空'}",
                    )
                elif not local_exists:
                    _add_log(state, f"{subscription.name} 决定下载：远端时间未变化但本地文件不存在")
                write_state()

            last_progress_write = 0.0

            def progress(downloaded: int, total: int | None) -> None:
                nonlocal last_progress_write
                now = time.monotonic()
                if total and downloaded < total and now - last_progress_write < 0.5:
                    return
                last_progress_write = now
                with state_lock:
                    state.current_index = index
                    state.current_name = subscription.name
                    state.current_downloaded = downloaded
                    state.current_total = total
                    state.message = f"正在下载：{subscription.name}"
                    write_state()

            downloaded_path = download_subscription(
                subscription,
                config.raw_data_dir,
                active_cookie,
                progress_callback=progress,
                page=page,
            )

            with state_lock:
                downloaded_paths.append(downloaded_path)
                state.updated_files.append(str(downloaded_path.resolve()))
                if remote_modified:
                    state.pending_modified_by_url[subscription.url] = remote_modified
                state.downloaded += 1
                state.percent = min(55, int(state.downloaded / max(1, state.total) * 55))
                state.updated_names.append(subscription.name)
                save_status(subscription, "已下载待解析")
                _add_log(
                    state,
                    f"{subscription.name} 下载完成；保存到={downloaded_path}；待提交远端最后修改={display_datetime(remote_modified) or '空'}",
                )
                write_state()
        except Exception as exc:  # noqa: BLE001 - one failed document must not abort the batch.
            with state_lock:
                state.failed += 1
                failed_subscriptions.append(subscription)
                if _is_cookie_error(exc):
                    state.cookie_refresh_required = True
                    cookie_failed_subscriptions.append(subscription)
                save_status(subscription, f"失败：{exc}")
                _add_log(state, f"{subscription.name} 更新失败：{exc}")
                write_state()

    try:
        def run_batch(batch: list[TencentDocSubscription]) -> None:
            workers = min(SUBSCRIPTION_MAX_WORKERS, max(1, len(batch)))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(run_one, index, subscription) for index, subscription in enumerate(batch, start=1)]
                for future in as_completed(futures):
                    future.result()

        run_batch(subscriptions)
        if state.origin == "auto" and cookie_failed_subscriptions and not _stop_requested(config):
            retry_by_url = {subscription.url: subscription for subscription in cookie_failed_subscriptions}
            retry_subscriptions = list(retry_by_url.values())
            cookie_failed_subscriptions.clear()
            failed_urls = set(retry_by_url)
            failed_subscriptions[:] = [item for item in failed_subscriptions if item.url not in failed_urls]
            state.status = "waiting_cookie"
            state.message = "Cookie 已失效，等待重新登录腾讯文档"
            _add_log(state, f"等待重新获取 Cookie，之后重试失败订阅 {len(retry_subscriptions)} 个")
            write_state()
            start_cookie_login(config, timeout_seconds=1800, poll_seconds=10)
            while not _stop_requested(config):
                login_state = read_login_state(config)
                if login_state.status == "completed":
                    active_cookie = load_saved_cookie(config)
                    if active_cookie:
                        break
                if login_state.status == "error":
                    raise RuntimeError(login_state.message or "等待重新获取 Cookie 超时")
                time.sleep(1)
            if _stop_requested(config):
                state.status = "stopped"
                state.message = "任务已停止"
                state.finished_at = _now()
                write_state()
                return
            state.status = "running"
            state.message = f"Cookie 获取成功，正在重试 {len(retry_subscriptions)} 个订阅"
            state.failed = max(0, state.failed - len(retry_subscriptions))
            state.cookie_refresh_required = False
            write_state()
            run_batch(retry_subscriptions)
            if cookie_failed_subscriptions:
                state.cookie_refresh_required = True

        if _stop_requested(config):
            with state_lock:
                state.status = "stopped"
                state.message = "任务已停止"
                state.finished_at = _now()
                write_state()

        save_subscriptions(subscriptions_path, next_subscriptions)
        if state.status == "stopped":
            return
        updated_name_set = set(state.updated_names)
        state.updated_names = [subscription.name for subscription in subscriptions if subscription.name in updated_name_set]
        if downloaded_paths:
            if _stop_requested(config):
                state.status = "stopped"
                state.finished_at = _now()
                state.message = "任务已停止，已下载文件尚未入库"
                write_state()
                return
            from customer_rag.pipeline import RagPipeline

            state.status = "rebuilding"
            state.percent = 58
            state.message = f"正在解析 {len(downloaded_paths)} 个更新文件"
            _add_log(state, f"下载完成：新增/更新 {len(downloaded_paths)} 个原始文件，开始解析")
            write_state()
            pipeline = RagPipeline(config)

            def parse_progress(percent: int, message: str) -> None:
                state.percent = 58 + int(max(0, min(percent, 100)) * 0.12)
                state.message = message
                write_state()

            import_scope = read_coordinator_state(config).subscription_import_scope
            if import_scope == "full":
                state.message = "正在强制全量解析原始文件"
                write_state()
                stats = pipeline.rebuild_corpus_from_raw(
                    rebuild_index=False,
                    force=True,
                    progress_callback=parse_progress,
                )
            else:
                tags_by_path = {
                    str(subscription_output_path(subscription, config.raw_data_dir).resolve()).lower(): subscription.tags
                    for subscription in subscriptions
                }
                path_tags = [
                    (path, tags_by_path.get(str(path.resolve()).lower(), []))
                    for path in downloaded_paths
                ]
                stats = pipeline.replace_files_with_tags(path_tags, rebuild_index=False)
            state.documents = int(stats.get("documents") or 0)
            state.items = int(stats.get("items") or 0)
            state.removed = int(stats.get("removed") or 0)
            state.percent = 70
            state.message = "语料解析完成，正在构建新索引"
            _add_log(state, f"解析完成：文档 {state.documents} 个，新增语料 {state.items} 条，替换旧语料 {state.removed} 条")
            write_state()

            def rebuild_progress(percent: int, message: str) -> None:
                state.percent = 70 + int(max(0, min(percent, 100)) * 0.29)
                state.message = message
                write_state()

            state.chunks = pipeline.rebuild_index(progress_callback=rebuild_progress)
            for subscription in subscriptions:
                pending_modified = state.pending_modified_by_url.get(subscription.url)
                if subscription.name in state.updated_names:
                    next_subscriptions = update_subscription_status(
                        next_subscriptions,
                        subscription.url,
                        "已同步",
                        last_modified=pending_modified,
                    )
            save_subscriptions(subscriptions_path, next_subscriptions)
            state.pending_modified_by_url = {}
            state.updated_files = []
            state.percent = 100
            state.message = f"{'、'.join(state.updated_names)} 已经订阅更新完毕"
            _add_log(state, f"索引原子切换完成：{state.chunks} 个片段")
        else:
            state.message = "订阅检查完成，无需下载。"
            state.percent = 100
        if state.failed:
            state.message += f"；本次有 {state.failed} 个订阅更新失败，将在下次定时任务重试"
        state.status = "completed"
        state.finished_at = _now()
        state.duration_seconds = max(0, int(time.monotonic() - started_monotonic))
        write_state()
    except Exception as exc:  # noqa: BLE001 - background jobs must persist diagnostics.
        state.status = "error"
        state.finished_at = _now()
        state.message = f"后台任务异常：{exc}"
        state.duration_seconds = max(0, int(time.monotonic() - started_monotonic))
        _add_log(state, state.message)
        _write_state(config, state)
        from customer_rag.task_coordinator import mark_stale_idle

        mark_stale_idle(config)
    finally:
        if state.cookie_refresh_required and state.origin != "auto":
            try:
                start_cookie_login(config)
            except RuntimeError:
                pass
        try:
            _worker_request_path(config, job_id).unlink(missing_ok=True)
        except OSError:
            pass
        release(config, job_id, f"auto:{state.status}" if state.origin == "auto" else state.status)


def _write_state(config: RagConfig, state: SubscriptionJobState) -> None:
    path = job_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(state), ensure_ascii=False, indent=2)
    last_error: OSError | None = None
    for attempt in range(8):
        tmp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        try:
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, path)
            return
        except OSError as exc:
            last_error = exc
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            time.sleep(0.08 * (attempt + 1))
    try:
        path.write_text(payload, encoding="utf-8")
    except OSError as exc:
        raise last_error or exc


def commit_pending_subscription_updates(config: RagConfig, subscriptions_path: Path) -> int:
    state = read_job_state(config)
    pending = dict(state.pending_modified_by_url or {})
    if not pending:
        return 0
    subscriptions = load_subscriptions(subscriptions_path)
    updated: list[TencentDocSubscription] = []
    changed = 0
    for subscription in subscriptions:
        pending_modified = pending.get(subscription.url)
        if pending_modified:
            updated.append(
                TencentDocSubscription(
                    name=subscription.name,
                    url=subscription.url,
                    tags=subscription.tags,
                    enabled=subscription.enabled,
                    last_updated=subscription.last_updated,
                    last_status="已同步",
                    last_modified=pending_modified,
                )
            )
            changed += 1
        else:
            updated.append(subscription)
    save_subscriptions(subscriptions_path, updated)
    state.pending_modified_by_url = {}
    state.updated_files = []
    state.message = "订阅远端最后修改时间已在索引重建完成后提交"
    _add_log(state, f"索引重建完成后提交订阅最后修改时间：{changed} 个")
    _write_state(config, state)
    return changed


def discard_pending_subscription_files(
    config: RagConfig,
    invalid_urls: set[str],
    invalid_paths: set[Path],
) -> None:
    if not invalid_urls and not invalid_paths:
        return
    state = read_job_state(config)
    invalid_path_keys = {str(path.resolve()).lower() for path in invalid_paths}
    state.updated_files = [
        path for path in state.updated_files if str(Path(path).resolve()).lower() not in invalid_path_keys
    ]
    for url in invalid_urls:
        state.pending_modified_by_url.pop(url, None)
    subscriptions = load_subscriptions(config.index_dir / "tencent_doc_subscriptions.json")
    invalid_names = {item.name for item in subscriptions if item.url in invalid_urls}
    state.updated_names = [name for name in state.updated_names if name not in invalid_names]
    _add_log(state, f"排除不完整下载文件 {len(invalid_paths)} 个，等待下次重新下载")
    _write_state(config, state)


def _add_log(state: SubscriptionJobState, message: str) -> None:
    state.logs.append(f"{now_display()} {message}")
    state.logs = state.logs[-300:]


def _stop_requested(config: RagConfig) -> bool:
    return stop_flag_path(config).exists()


def _is_cookie_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "cookie",
            "登录",
            "未授权",
            "无权限",
            "access denied",
            "http 401",
            "http 403",
        )
    )


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_config_for_pid_check() -> RagConfig:
    from customer_rag.config import load_config

    return load_config()
