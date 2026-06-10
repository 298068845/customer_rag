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
    index_error: str = ""
    message: str = ""
    updated_files: list[str] = field(default_factory=list)
    pending_modified_by_url: dict[str, str] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)


_lock = threading.Lock()
_worker: threading.Thread | None = None


def job_state_path(config: RagConfig) -> Path:
    return config.index_dir / "subscription_update_job.json"


def stop_flag_path(config: RagConfig) -> Path:
    return config.index_dir / "subscription_update_job.stop"


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
    return SubscriptionJobState(**defaults)


def is_subscription_worker_alive() -> bool:
    return bool(_worker and _worker.is_alive())


def recover_interrupted_subscription_job(config: RagConfig) -> SubscriptionJobState:
    state = read_job_state(config)
    if state.status in {"running", "rebuilding", "stopping"} and not is_subscription_worker_alive():
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
) -> SubscriptionJobState:
    global _worker
    with _lock:
        state = recover_interrupted_subscription_job(config)
        if state.status in {"running", "rebuilding", "stopping"} and _worker and _worker.is_alive():
            return state
        stop_flag_path(config).unlink(missing_ok=True)
        job_id = uuid4().hex
        state = SubscriptionJobState(
            job_id=job_id,
            status="running",
            started_at=_now(),
            total=len(subscriptions),
            message="订阅下载已启动",
        )
        _write_state(config, state)
        _worker = threading.Thread(
            target=_run_subscription_job,
            args=(config, subscriptions_path, subscriptions, cookie, job_id),
            daemon=True,
        )
        _worker.start()
        return state


def request_stop_subscription_job(config: RagConfig) -> None:
    stop_flag_path(config).parent.mkdir(parents=True, exist_ok=True)
    stop_flag_path(config).write_text("stop", encoding="utf-8")
    state = read_job_state(config)
    if state.status == "running":
        state.status = "stopping"
        state.message = "正在停止，当前安全点结束后退出"
        _write_state(config, state)


def is_subscription_job_running(config: RagConfig) -> bool:
    global _worker
    state = read_job_state(config)
    return state.status in {"running", "rebuilding", "stopping"} and bool(_worker and _worker.is_alive())


def _run_subscription_job(
    config: RagConfig,
    subscriptions_path: Path,
    subscriptions: list[TencentDocSubscription],
    cookie: str,
    job_id: str,
) -> None:
    state = read_job_state(config)
    state_lock = threading.Lock()
    next_subscriptions = subscriptions
    downloaded_paths: list[Path] = []

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
        nonlocal downloaded_paths
        if _stop_requested(config):
            return

        with state_lock:
            state.current_index = index
            state.current_name = subscription.name
            state.current_downloaded = 0
            state.current_total = None
            state.message = f"正在更新：{subscription.name}"
            save_status(subscription, "更新中")
            _add_log(state, f"{subscription.name} 开始检查；本地记录最后修改={subscription.last_modified or '空'}")
            write_state()

        try:
            page = fetch_subscription_page(subscription, cookie)
            remote_modified = fetch_subscription_last_modified(subscription, cookie, page=page)
            output_path = subscription_output_path(subscription, config.raw_data_dir)
            local_exists = output_path.exists()

            with state_lock:
                _add_log(
                    state,
                    (
                        f"{subscription.name} 远端最后修改={remote_modified or '未读取到'}；"
                        f"本地文件={'存在' if local_exists else '不存在'}；路径={output_path}"
                    ),
                )
                write_state()

            if remote_modified and remote_modified == subscription.last_modified and local_exists:
                with state_lock:
                    state.skipped += 1
                    save_status(subscription, "跳过：文件未变化", last_modified=remote_modified)
                    _add_log(
                        state,
                        f"{subscription.name} 跳过：远端最后修改与已入库记录一致 ({remote_modified})，且本地文件存在",
                    )
                    write_state()
                return

            with state_lock:
                if not remote_modified:
                    _add_log(state, f"{subscription.name} 决定下载：未读取到远端最后修改时间，无法确认本地是否最新")
                elif remote_modified != subscription.last_modified:
                    _add_log(
                        state,
                        f"{subscription.name} 决定下载：远端最后修改 {remote_modified} != 已入库记录 {subscription.last_modified or '空'}",
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
                cookie,
                progress_callback=progress,
                page=page,
            )

            with state_lock:
                downloaded_paths.append(downloaded_path)
                state.updated_files.append(str(downloaded_path.resolve()))
                if remote_modified:
                    state.pending_modified_by_url[subscription.url] = remote_modified
                state.downloaded += 1
                save_status(subscription, "已下载待解析")
                _add_log(
                    state,
                    f"{subscription.name} 下载完成；保存到={downloaded_path}；待提交远端最后修改={remote_modified or '空'}",
                )
                write_state()
        except RuntimeError as exc:
            with state_lock:
                state.failed += 1
                save_status(subscription, f"失败：{exc}")
                _add_log(state, f"{subscription.name} 更新失败：{exc}")
                write_state()

    try:
        workers = min(SUBSCRIPTION_MAX_WORKERS, max(1, len(subscriptions)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(run_one, index, subscription)
                for index, subscription in enumerate(subscriptions, start=1)
            ]
            for future in as_completed(futures):
                future.result()
                if _stop_requested(config):
                    with state_lock:
                        state.status = "stopped"
                        state.message = "任务已停止"
                        state.finished_at = _now()
                        write_state()
                    break

        save_subscriptions(subscriptions_path, next_subscriptions)
        if state.status == "stopped":
            return
        state.status = "completed"
        state.finished_at = _now()
        if downloaded_paths:
            state.message = "下载完成。请在下方点击重新解析文件完成后再重构索引。"
            _add_log(state, f"下载完成：新增/更新 {len(downloaded_paths)} 个原始文件，等待手动解析和重构索引")
        else:
            state.message = "订阅检查完成，无需下载。"
        write_state()
    except Exception as exc:  # noqa: BLE001 - background jobs must persist diagnostics.
        state.status = "error"
        state.finished_at = _now()
        state.message = f"后台任务异常：{exc}"
        _add_log(state, state.message)
        _write_state(config, state)


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


def _add_log(state: SubscriptionJobState, message: str) -> None:
    state.logs.append(f"{_now()} {message}")
    state.logs = state.logs[-300:]


def _stop_requested(config: RagConfig) -> bool:
    return stop_flag_path(config).exists()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
