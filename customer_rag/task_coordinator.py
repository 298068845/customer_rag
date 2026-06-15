from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from customer_rag.config import RagConfig


DEFAULT_AUTO_INTERVAL_MINUTES = 20


@dataclass
class TaskCoordinatorState:
    active_kind: str = ""
    active_job_id: str = ""
    active_started_at: str = ""
    auto_enabled: bool = False
    auto_interval_minutes: int = DEFAULT_AUTO_INTERVAL_MINUTES
    next_auto_at: str = ""
    last_activity_finished_at: str = ""
    last_auto_attempt_at: str = ""
    last_auto_result: str = ""
    scheduler_pid: int = 0
    subscription_import_scope: str = "pending"


_lock = threading.RLock()


def state_path(config: RagConfig) -> Path:
    return config.index_dir / "task_coordinator.json"


def read_state(config: RagConfig) -> TaskCoordinatorState:
    path = state_path(config)
    if not path.exists():
        return TaskCoordinatorState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return TaskCoordinatorState()
    defaults = asdict(TaskCoordinatorState())
    defaults.update({key: payload.get(key, value) for key, value in defaults.items()})
    return TaskCoordinatorState(**defaults)


def write_state(config: RagConfig, state: TaskCoordinatorState) -> None:
    path = state_path(config)
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


def try_acquire(config: RagConfig, kind: str, job_id: str) -> bool:
    with _lock:
        state = read_state(config)
        if state.active_kind:
            return False
        state.active_kind = kind
        state.active_job_id = job_id
        state.active_started_at = _now()
        if state.auto_enabled:
            state.next_auto_at = ""
        write_state(config, state)
        return True


def release(config: RagConfig, job_id: str, result: str = "completed") -> None:
    with _lock:
        state = read_state(config)
        if state.active_job_id != job_id:
            return
        state.active_kind = ""
        state.active_job_id = ""
        state.active_started_at = ""
        state.last_activity_finished_at = _now()
        if state.auto_enabled and result in {"completed", "auto:completed"}:
            state.next_auto_at = _after_minutes(state.auto_interval_minutes)
        state.last_auto_result = result if result.startswith("auto:") else state.last_auto_result
        write_state(config, state)


def mark_stale_idle(config: RagConfig) -> None:
    with _lock:
        state = read_state(config)
        if not state.active_kind:
            return
        state.active_kind = ""
        state.active_job_id = ""
        state.active_started_at = ""
        if state.auto_enabled and not state.next_auto_at:
            state.next_auto_at = _after_minutes(state.auto_interval_minutes)
        write_state(config, state)


def set_auto_enabled(config: RagConfig, enabled: bool, interval_minutes: int = DEFAULT_AUTO_INTERVAL_MINUTES) -> TaskCoordinatorState:
    with _lock:
        state = read_state(config)
        was_enabled = state.auto_enabled
        state.auto_enabled = enabled
        state.auto_interval_minutes = max(1, int(interval_minutes))
        if enabled and not was_enabled:
            state.next_auto_at = _now()
        elif enabled and not state.next_auto_at:
            state.next_auto_at = _now()
        elif not enabled:
            state.next_auto_at = ""
        write_state(config, state)
        return state


def set_subscription_import_scope(config: RagConfig, scope: str) -> TaskCoordinatorState:
    with _lock:
        state = read_state(config)
        state.subscription_import_scope = "full" if scope == "full" else "pending"
        write_state(config, state)
        return state


def schedule_auto_now_on_start(config: RagConfig, *, run_immediately: bool = True) -> TaskCoordinatorState:
    with _lock:
        state = read_state(config)
        current_pid = os.getpid()
        if state.auto_enabled and run_immediately and not state.next_auto_at:
            state.next_auto_at = _now()
        state.scheduler_pid = current_pid
        write_state(config, state)
        return state


def defer_auto(config: RagConfig, result: str) -> TaskCoordinatorState:
    with _lock:
        state = read_state(config)
        state.last_auto_attempt_at = _now()
        state.last_auto_result = result
        if state.auto_enabled:
            state.next_auto_at = _after_minutes(state.auto_interval_minutes)
        write_state(config, state)
        return state


def auto_is_due(state: TaskCoordinatorState, now: datetime | None = None) -> bool:
    if not state.auto_enabled or not state.next_auto_at:
        return False
    try:
        due = datetime.fromisoformat(state.next_auto_at)
    except ValueError:
        return True
    return (now or datetime.now()) >= due


@contextmanager
def activity(config: RagConfig, kind: str, job_id: str):
    if not try_acquire(config, kind, job_id):
        raise RuntimeError("另一个导入或订阅任务正在运行")
    try:
        yield
    finally:
        release(config, job_id)


def _after_minutes(minutes: int) -> str:
    return (datetime.now() + timedelta(minutes=max(1, minutes))).isoformat(timespec="seconds")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
