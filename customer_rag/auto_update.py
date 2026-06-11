from __future__ import annotations

import threading
import time

from customer_rag.config import RagConfig
from customer_rag.cookie_login import has_saved_cookie, load_saved_cookie, start_cookie_login
from customer_rag.subscription_jobs import start_subscription_job
from customer_rag.task_coordinator import auto_is_due, defer_auto, read_state, schedule_auto_now_on_start
from customer_rag.tencent_docs import load_subscriptions


_lock = threading.Lock()
_worker: threading.Thread | None = None


def ensure_auto_update_scheduler(config: RagConfig, *, run_immediately: bool = True) -> None:
    global _worker
    with _lock:
        if _worker and _worker.is_alive():
            return
        schedule_auto_now_on_start(config, run_immediately=run_immediately)
        _worker = threading.Thread(target=_run_scheduler, args=(config,), daemon=True)
        _worker.start()


def run_auto_update_check(config: RagConfig) -> str:
    state = read_state(config)
    if not auto_is_due(state):
        return "not_due"
    if state.active_kind:
        defer_auto(config, f"skipped_busy:{state.active_kind}")
        return "skipped_busy"
    subscriptions_path = config.index_dir / "tencent_doc_subscriptions.json"
    enabled = [item for item in load_subscriptions(subscriptions_path) if item.enabled]
    if not enabled:
        defer_auto(config, "skipped_no_subscriptions")
        return "skipped_no_subscriptions"
    if not has_saved_cookie(config):
        start_cookie_login(config)
        defer_auto(config, "waiting_cookie")
        return "waiting_cookie"
    result = start_subscription_job(
        config,
        subscriptions_path,
        enabled,
        load_saved_cookie(config),
        origin="auto",
    )
    if result.status == "busy":
        defer_auto(config, "skipped_busy")
        return "skipped_busy"
    return "started"


def _run_scheduler(config: RagConfig) -> None:
    while True:
        try:
            run_auto_update_check(config)
        except Exception:
            defer_auto(config, "scheduler_error")
        time.sleep(2)
