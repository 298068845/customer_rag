from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from customer_rag.config import RagConfig
from customer_rag.pipeline import RagPipeline
from customer_rag.tencent_docs import (
    TencentDocSubscription,
    download_subscription,
    fetch_subscription_last_modified,
    save_subscriptions,
    subscription_output_path,
    update_subscription_status,
)


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


def start_subscription_job(
    config: RagConfig,
    subscriptions_path: Path,
    subscriptions: list[TencentDocSubscription],
    cookie: str,
) -> SubscriptionJobState:
    global _worker
    with _lock:
        state = read_job_state(config)
        if state.status in {"running", "rebuilding", "stopping"} and _worker and _worker.is_alive():
            return state
        stop_flag_path(config).unlink(missing_ok=True)
        job_id = uuid4().hex
        state = SubscriptionJobState(
            job_id=job_id,
            status="running",
            started_at=_now(),
            total=len(subscriptions),
            message="后台更新已启动",
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
    downloaded_paths: list[tuple[Path, list[str]]] = []
    next_subscriptions = subscriptions
    state = read_job_state(config)
    try:
        for index, subscription in enumerate(subscriptions, start=1):
            if _stop_requested(config):
                state.status = "stopped"
                state.message = "任务已停止"
                break
            state.current_index = index
            state.current_name = subscription.name
            state.current_downloaded = 0
            state.current_total = None
            state.message = f"正在更新：{subscription.name}"
            _add_log(state, f"{subscription.name} 开始检查")
            _write_state(config, state)

            try:
                remote_modified = fetch_subscription_last_modified(subscription, cookie)
                output_path = subscription_output_path(subscription, config.raw_data_dir)
                if remote_modified and remote_modified == subscription.last_modified and output_path.exists():
                    state.skipped += 1
                    next_subscriptions = update_subscription_status(
                        next_subscriptions,
                        subscription.url,
                        "跳过：文件未变化",
                        last_modified=remote_modified,
                    )
                    _add_log(state, f"{subscription.name} 跳过：文件未变化")
                    _write_state(config, state)
                    continue

                def progress(downloaded: int, total: int | None) -> None:
                    state.current_downloaded = downloaded
                    state.current_total = total
                    state.message = f"正在下载：{subscription.name}"
                    _write_state(config, state)

                downloaded_path = download_subscription(
                    subscription,
                    config.raw_data_dir,
                    cookie,
                    progress_callback=progress,
                )
                downloaded_paths.append((downloaded_path, subscription.tags))
                state.downloaded += 1
                next_subscriptions = update_subscription_status(
                    next_subscriptions,
                    subscription.url,
                    "成功",
                    last_modified=remote_modified,
                )
                _add_log(state, f"{subscription.name} 下载完成")
                _write_state(config, state)
            except RuntimeError as exc:
                state.failed += 1
                next_subscriptions = update_subscription_status(next_subscriptions, subscription.url, f"失败：{exc}")
                _add_log(state, f"{subscription.name} 更新失败：{exc}")
                _write_state(config, state)

        save_subscriptions(subscriptions_path, next_subscriptions)
        if state.status == "stopped":
            state.finished_at = _now()
            _write_state(config, state)
            return
        if downloaded_paths:
            state.status = "rebuilding"
            state.current_name = "重建向量索引"
            state.message = "正在替换导入订阅语料并重建索引"
            _write_state(config, state)
            stats = RagPipeline(config).replace_files_with_tags(downloaded_paths)
            state.removed = int(stats.get("removed") or 0)
            state.documents = int(stats.get("documents") or 0)
            state.items = int(stats.get("items") or 0)
            state.chunks = int(stats.get("chunks") or 0)
            state.index_error = str(stats.get("index_error") or "")
            _add_log(state, f"重建完成：新增 {state.items} 条语料，索引 {state.chunks} 个片段")
        state.status = "completed"
        state.finished_at = _now()
        state.message = "订阅更新完成"
        _write_state(config, state)
    except Exception as exc:  # noqa: BLE001 - background jobs must persist diagnostics.
        state.status = "error"
        state.finished_at = _now()
        state.message = f"后台任务异常：{exc}"
        _add_log(state, state.message)
        _write_state(config, state)


def _write_state(config: RagConfig, state: SubscriptionJobState) -> None:
    path = job_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _add_log(state: SubscriptionJobState, message: str) -> None:
    state.logs.append(f"{_now()} {message}")
    state.logs = state.logs[-30:]


def _stop_requested(config: RagConfig) -> bool:
    return stop_flag_path(config).exists()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
