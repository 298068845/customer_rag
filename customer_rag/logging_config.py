from __future__ import annotations

import ctypes
import json
import logging
import os
import platform
import shutil
import sys
import threading
import time
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


RETENTION_DAYS = 10
MAX_FILE_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 12
MAX_LOG_DIR_BYTES = 500 * 1024 * 1024

_CONFIGURED_ROOTS: set[Path] = set()
_LAST_CLEANUP: dict[Path, float] = {}
_CONFIG_LOCK = threading.Lock()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="seconds"),
            "level": record.levelname,
            "component": getattr(record, "component", record.name.rsplit(".", 1)[-1]),
            "event": getattr(record, "event", record.getMessage()),
            "pid": os.getpid(),
            "thread": record.threadName,
            "message": record.getMessage(),
        }
        for key in ("job_id", "query_id"):
            value = getattr(record, key, "")
            if value:
                payload[key] = value
        context = getattr(record, "context", None)
        if context:
            payload["context"] = _json_safe(context)
        system = getattr(record, "system", None)
        if system:
            payload["system"] = _json_safe(system)
        if record.exc_info:
            exc_type = record.exc_info[0]
            exc_value = record.exc_info[1]
            payload["error_type"] = exc_type.__name__ if exc_type else ""
            payload["error"] = str(exc_value or "")
            payload["traceback"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class MaxLevelFilter(logging.Filter):
    def __init__(self, max_level: int) -> None:
        super().__init__()
        self.max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.max_level


def configure_logging(project_root: str | Path | None = None, *, component: str | None = None) -> Path:
    root = Path(project_root or Path.cwd()).resolve()
    with _CONFIG_LOCK:
        configured = root in _CONFIGURED_ROOTS
        log_dir = logs_dir(root)
        if not configured:
            log_dir.mkdir(parents=True, exist_ok=True)
            cleanup_logs(root)
            _CONFIGURED_ROOTS.add(root)
    if component:
        get_logger(component, root)
    return log_dir


def logs_dir(project_root: str | Path | None = None) -> Path:
    return Path(project_root or Path.cwd()).resolve() / "logs"


def get_logger(component: str, project_root: str | Path | None = None) -> logging.Logger:
    root = Path(project_root or Path.cwd()).resolve()
    configure_logging(root)
    _cleanup_if_due(root)
    logger = logging.getLogger(f"customer_rag.{component}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if getattr(logger, "_customer_rag_log_root", None) == str(root):
        return logger
    logger.handlers.clear()
    formatter = JsonFormatter()
    component_file = logs_dir(root) / f"{component}.log"
    error_file = logs_dir(root) / f"{component}.error.log"

    normal_handler = RotatingFileHandler(
        component_file,
        maxBytes=MAX_FILE_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    normal_handler.setFormatter(formatter)
    normal_handler.setLevel(logging.DEBUG)
    normal_handler.addFilter(MaxLevelFilter(logging.WARNING))

    error_handler = RotatingFileHandler(
        error_file,
        maxBytes=MAX_FILE_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)

    logger.addHandler(normal_handler)
    logger.addHandler(error_handler)
    setattr(logger, "_customer_rag_log_root", str(root))
    return logger


def log_event(
    component: str,
    event: str,
    message: str,
    *,
    level: int = logging.INFO,
    project_root: str | Path | None = None,
    context: dict[str, Any] | None = None,
    job_id: str = "",
    query_id: str = "",
    include_system: bool = False,
) -> None:
    extra = {
        "component": component,
        "event": event,
        "context": context or {},
        "job_id": job_id,
        "query_id": query_id,
        "system": runtime_snapshot(project_root) if include_system else {},
    }
    get_logger(component, project_root).log(level, message, extra=extra)


def log_exception(
    component: str,
    event: str,
    message: str,
    exc: BaseException,
    *,
    project_root: str | Path | None = None,
    context: dict[str, Any] | None = None,
    job_id: str = "",
    query_id: str = "",
) -> None:
    extra = {
        "component": component,
        "event": event,
        "context": context or {},
        "job_id": job_id,
        "query_id": query_id,
        "system": runtime_snapshot(project_root),
    }
    get_logger(component, project_root).error(message, exc_info=(type(exc), exc, exc.__traceback__), extra=extra)


def log_system_snapshot(project_root: str | Path | None = None) -> None:
    log_event(
        "app",
        "system_snapshot",
        "system snapshot",
        project_root=project_root,
        context=system_snapshot(project_root),
    )


def install_exception_hooks(project_root: str | Path | None = None, *, component: str = "app") -> None:
    root = Path(project_root or Path.cwd()).resolve()
    configure_logging(root)

    def excepthook(exc_type, exc, tb) -> None:
        log_exception(
            component,
            "uncaught_exception",
            "uncaught exception",
            exc,
            project_root=root,
        )
        sys.__excepthook__(exc_type, exc, tb)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        log_exception(
            component,
            "uncaught_thread_exception",
            f"uncaught exception in thread {args.thread.name}",
            args.exc_value,
            project_root=root,
            context={"thread": args.thread.name},
        )
        if hasattr(threading, "__excepthook__"):
            threading.__excepthook__(args)

    sys.excepthook = excepthook
    if hasattr(threading, "excepthook"):
        threading.excepthook = thread_hook


def cleanup_logs(project_root: str | Path | None = None) -> None:
    log_dir = logs_dir(project_root)
    log_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    cutoff = now - RETENTION_DAYS * 24 * 60 * 60
    files: list[Path] = []
    for path in log_dir.glob("*.log*"):
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                continue
            files.append(path)
        except OSError:
            continue
    _enforce_log_dir_limit(files)
    _LAST_CLEANUP[log_dir.parent] = time.time()


def _cleanup_if_due(root: Path) -> None:
    now = time.time()
    if now - _LAST_CLEANUP.get(root, 0) < 24 * 60 * 60:
        return
    with _CONFIG_LOCK:
        if now - _LAST_CLEANUP.get(root, 0) < 24 * 60 * 60:
            return
        cleanup_logs(root)


def system_snapshot(project_root: str | Path | None = None) -> dict[str, Any]:
    root = Path(project_root or Path.cwd()).resolve()
    snapshot = {
        "os": platform.platform(),
        "python": platform.python_version(),
        "executable": sys.executable,
        "project_root": str(root),
        "cpu_count": os.cpu_count(),
        "memory": _memory_snapshot(),
        "disk": _disk_snapshot(root),
    }
    try:
        from customer_rag.config import load_config

        config = load_config(root / "config.yaml")
        snapshot["config"] = {
            "raw_data_dir": str((root / config.raw_data_dir).resolve() if not config.raw_data_dir.is_absolute() else config.raw_data_dir),
            "index_dir": str((root / config.index_dir).resolve() if not config.index_dir.is_absolute() else config.index_dir),
            "embedding_model_path": str(config.embedding_model_path),
            "llm_model_path": str(config.llm_model_path),
            "llm_backend": config.llm.backend,
            "llama_server_url": config.llm.llama_server_url,
            "llama_server_port": config.llm.llama_server_port,
        }
    except Exception as exc:  # noqa: BLE001 - diagnostics must not break callers.
        snapshot["config_error"] = str(exc)
    return snapshot


def runtime_snapshot(project_root: str | Path | None = None) -> dict[str, Any]:
    root = Path(project_root or Path.cwd()).resolve()
    return {
        "memory": _memory_snapshot(),
        "disk": _disk_snapshot(root),
    }


def _memory_snapshot() -> dict[str, Any]:
    if os.name != "nt":
        return {}

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {}
    return {
        "load_percent": int(status.dwMemoryLoad),
        "total_mb": int(status.ullTotalPhys / 1024 / 1024),
        "available_mb": int(status.ullAvailPhys / 1024 / 1024),
    }


def _disk_snapshot(path: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return {}
    return {
        "path": str(path),
        "total_gb": round(usage.total / 1024 / 1024 / 1024, 2),
        "free_gb": round(usage.free / 1024 / 1024 / 1024, 2),
    }


def _enforce_log_dir_limit(files: list[Path]) -> None:
    entries: list[tuple[float, int, Path]] = []
    total = 0
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        total += stat.st_size
        entries.append((stat.st_mtime, stat.st_size, path))
    if total <= MAX_LOG_DIR_BYTES:
        return
    for _, size, path in sorted(entries):
        try:
            path.unlink()
            total -= size
        except OSError:
            continue
        if total <= MAX_LOG_DIR_BYTES:
            break


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): _json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(item) for item in value]
        return str(value)
