from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from customer_rag.config import RagConfig
from customer_rag.pipeline import RagPipeline


@dataclass
class RawJobState:
    job_id: str = ""
    task: str = ""
    status: str = "idle"
    started_at: str = ""
    finished_at: str = ""
    percent: int = 0
    message: str = ""
    documents: int = 0
    items: int = 0
    chunks: int = 0
    index_error: str = ""
    committed_subscriptions: int = 0
    error: str = ""
    logs: list[str] = field(default_factory=list)


_lock = threading.Lock()
_worker: threading.Thread | None = None


def raw_job_state_path(config: RagConfig) -> Path:
    return config.index_dir / "raw_job_state.json"


def read_raw_job_state(config: RagConfig) -> RawJobState:
    path = raw_job_state_path(config)
    if not path.exists():
        return RawJobState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return RawJobState(status="error", error="任务状态文件读取失败")
    defaults = asdict(RawJobState())
    defaults.update({key: payload.get(key, value) for key, value in defaults.items()})
    if defaults["logs"] is None:
        defaults["logs"] = []
    return RawJobState(**defaults)


def is_raw_job_worker_alive() -> bool:
    return bool(_worker and _worker.is_alive())


def recover_interrupted_raw_job(config: RagConfig) -> RawJobState:
    state = read_raw_job_state(config)
    if state.status == "running" and not is_raw_job_worker_alive():
        state.status = "error"
        state.finished_at = _now()
        state.error = "任务已中断：服务重启或后台 worker 已不存在"
        state.message = "任务已中断"
        _add_log(state, state.error)
        _write_state(config, state)
    return state


def start_raw_job(config: RagConfig, task: str) -> RawJobState:
    global _worker
    if task not in {"rebuild_raw", "rebuild_index"}:
        return RawJobState(status="error", error=f"未知任务：{task}")
    with _lock:
        state = recover_interrupted_raw_job(config)
        if state.status == "running" and _worker and _worker.is_alive():
            return state
        state = RawJobState(
            job_id=uuid4().hex,
            task=task,
            status="running",
            started_at=_now(),
            percent=0,
            message=_task_label(task) + "已启动",
        )
        _write_state(config, state)
        _worker = threading.Thread(target=_run_raw_job, args=(config, task, state.job_id), daemon=True)
        _worker.start()
        return state


def _run_raw_job(config: RagConfig, task: str, job_id: str) -> None:
    state = read_raw_job_state(config)

    def update(percent: int, message: str) -> None:
        if read_raw_job_state(config).job_id != job_id:
            return
        state.percent = max(0, min(percent, 100))
        state.message = message
        _write_state(config, state)

    try:
        pipeline = RagPipeline(config)
        if task == "rebuild_raw":
            pending_path_tags = _pending_subscription_path_tags(config)
            if pending_path_tags:
                update(5, f"正在解析本次下载文件：{len(pending_path_tags)} 个")
                stats = pipeline.replace_files_with_tags(pending_path_tags, rebuild_index=False)
                update(100, f"本次下载文件解析完成：{len(pending_path_tags)} 个")
            else:
                stats = pipeline.rebuild_corpus_from_raw(rebuild_index=False, progress_callback=update)
            state.documents = int(stats.get("documents") or 0)
            state.items = int(stats.get("items") or 0)
            state.chunks = int(stats.get("chunks") or 0)
            state.index_error = str(stats.get("index_error") or "")
        else:
            chunks = pipeline.rebuild_index(progress_callback=update)
            state.chunks = int(chunks or 0)
            committed = _commit_pending_subscription_updates(config)
            state.committed_subscriptions = committed
            if committed:
                _add_log(state, f"已提交订阅最后修改时间：{committed} 个")
        if read_raw_job_state(config).job_id != job_id:
            return
        state.status = "completed"
        state.percent = 100
        state.finished_at = _now()
        state.message = _task_label(task) + "完成"
        _add_log(state, state.message)
        _write_state(config, state)
    except Exception as exc:  # noqa: BLE001 - user-facing background job boundary.
        if read_raw_job_state(config).job_id != job_id:
            return
        state.status = "error"
        state.finished_at = _now()
        state.error = str(exc)
        state.message = _task_label(task) + "失败"
        _add_log(state, f"{state.message}：{exc}")
        _write_state(config, state)


def _write_state(config: RagConfig, state: RawJobState) -> None:
    path = raw_job_state_path(config)
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


def _add_log(state: RawJobState, message: str) -> None:
    state.logs.append(f"{_now()} {message}")
    state.logs = state.logs[-30:]


def _task_label(task: str) -> str:
    if task == "rebuild_raw":
        return "重新解析原始文件"
    if task == "rebuild_index":
        return "重建向量索引"
    return "任务"


def _commit_pending_subscription_updates(config: RagConfig) -> int:
    from customer_rag.subscription_jobs import commit_pending_subscription_updates

    return commit_pending_subscription_updates(config, config.index_dir / "tencent_doc_subscriptions.json")


def _pending_subscription_path_tags(config: RagConfig) -> list[tuple[Path, list[str]]]:
    from customer_rag.subscription_jobs import read_job_state
    from customer_rag.tencent_docs import load_subscriptions, subscription_output_path

    job_state = read_job_state(config)
    pending_files = {_normalize_path_key(Path(path)) for path in (job_state.updated_files or [])}
    if not pending_files:
        return []
    subscriptions = load_subscriptions(config.index_dir / "tencent_doc_subscriptions.json")
    result: list[tuple[Path, list[str]]] = []
    for subscription in subscriptions:
        output_path = subscription_output_path(subscription, config.raw_data_dir)
        candidates = _path_keys(output_path)
        if candidates.intersection(pending_files):
            result.append((output_path, subscription.tags))
    return result


def _path_keys(path: Path) -> set[str]:
    keys = {str(path), str(path.resolve()), path.name}
    try:
        keys.add(str(path.resolve().relative_to(Path.cwd().resolve())))
    except ValueError:
        pass
    return {_normalize_path_key(value) for value in keys}


def _normalize_path_key(value: str | Path) -> str:
    return str(value).replace("\\", "/").lower()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
