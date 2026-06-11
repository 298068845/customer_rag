from __future__ import annotations

import tempfile
import time
import sys
import io
import zipfile
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.environ["CUSTOMER_RAG_INLINE_WORKER"] = "1"

from customer_rag.auto_update import run_auto_update_check
from customer_rag.browser_cookies import BrowserCookieResult
from customer_rag.config import LlmConfig, RagConfig
from customer_rag.cookie_login import CookieLoginState, _poll_cookie, capture_cookie, load_saved_cookie, open_cookie_login
from customer_rag.raw_jobs import read_raw_job_state, start_raw_job
from customer_rag.subscription_jobs import is_subscription_worker_alive, read_job_state, start_subscription_job
from customer_rag.task_coordinator import (
    TaskCoordinatorState,
    read_state,
    set_auto_enabled,
    set_subscription_import_scope,
    schedule_auto_now_on_start,
    try_acquire,
    write_state,
)
from customer_rag.tencent_docs import TencentDocSubscription, save_subscriptions
from customer_rag.tencent_docs import _write_xlsx_atomically


def make_config(root: Path) -> RagConfig:
    return RagConfig(
        raw_data_dir=root / "raw",
        index_dir=root / "index",
        embedding_model_path=root / "model",
        llm_model_path=root / "model.gguf",
        llm=LlmConfig(),
    )


def wait_until(done, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if done():
            return
        time.sleep(0.02)
    raise AssertionError("worker did not finish")


def test_scheduler_rules(config: RagConfig) -> None:
    state = set_auto_enabled(config, True, 20)
    assert state.auto_enabled and state.next_auto_at
    assert datetime.fromisoformat(state.next_auto_at) <= datetime.now() + timedelta(seconds=2)

    state.active_kind = "manual_import"
    state.active_job_id = "manual"
    state.next_auto_at = (datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds")
    write_state(config, state)
    assert run_auto_update_check(config) == "skipped_busy"
    deferred = read_state(config)
    assert datetime.fromisoformat(deferred.next_auto_at) > datetime.now() + timedelta(minutes=19)

    deferred.active_kind = ""
    deferred.active_job_id = ""
    deferred.next_auto_at = datetime.now().isoformat(timespec="seconds")
    write_state(config, deferred)
    subscriptions = [TencentDocSubscription(name="测试订阅", url="https://docs.qq.com/test")]
    with patch("customer_rag.auto_update.load_subscriptions", return_value=subscriptions), patch(
        "customer_rag.auto_update.has_saved_cookie", return_value=True
    ), patch("customer_rag.auto_update.load_saved_cookie", return_value="cookie=ok"), patch(
        "customer_rag.auto_update.start_subscription_job"
    ) as start:
        start.return_value.status = "running"
        assert run_auto_update_check(config) == "started"
        start.assert_called_once()


def test_incomplete_xlsx_does_not_replace_existing(config: RagConfig) -> None:
    output = config.raw_data_dir / "existing.xlsx"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"existing")
    try:
        _write_xlsx_atomically(output, b"PK\x03\x04incomplete")
    except RuntimeError:
        pass
    else:
        raise AssertionError("incomplete xlsx should be rejected")
    assert output.read_bytes() == b"existing"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook />")
    _write_xlsx_atomically(output, buffer.getvalue())
    assert zipfile.is_zipfile(output)


def test_manual_reschedules_auto(config: RagConfig) -> None:
    state = set_auto_enabled(config, True, 20)
    state.next_auto_at = datetime.now().isoformat(timespec="seconds")
    write_state(config, state)
    assert try_acquire(config, "manual_import", "manual-job")
    scheduled = read_state(config)
    assert datetime.fromisoformat(scheduled.next_auto_at) > datetime.now() + timedelta(minutes=19)
    scheduled.active_kind = ""
    scheduled.active_job_id = ""
    write_state(config, scheduled)


def test_scheduler_init_is_idempotent(config: RagConfig) -> None:
    state = set_auto_enabled(config, True, 20)
    schedule_auto_now_on_start(config)
    state = read_state(config)
    state.next_auto_at = (datetime.now() + timedelta(minutes=20)).isoformat(timespec="seconds")
    write_state(config, state)

    repeated = schedule_auto_now_on_start(config)
    assert datetime.fromisoformat(repeated.next_auto_at) > datetime.now() + timedelta(minutes=19)

    state.scheduler_pid = 0
    write_state(config, state)
    deferred = schedule_auto_now_on_start(config, run_immediately=False)
    assert datetime.fromisoformat(deferred.next_auto_at) > datetime.now() + timedelta(minutes=19)

    deferred.scheduler_pid = 0
    write_state(config, deferred)
    restarted = schedule_auto_now_on_start(config, run_immediately=True)
    assert datetime.fromisoformat(restarted.next_auto_at) > datetime.now() + timedelta(minutes=19)


def test_coordinator_write_retries_windows_file_lock(config: RagConfig) -> None:
    state = TaskCoordinatorState(auto_enabled=True, next_auto_at=datetime.now().isoformat(timespec="seconds"))
    real_replace = __import__("os").replace
    attempts = 0

    def flaky_replace(source, target):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError(5, "拒绝访问", str(target))
        return real_replace(source, target)

    with patch("customer_rag.task_coordinator.os.replace", side_effect=flaky_replace):
        write_state(config, state)
    assert attempts == 3
    assert read_state(config).auto_enabled


def test_subscription_import_scope(config: RagConfig) -> None:
    assert set_subscription_import_scope(config, "full").subscription_import_scope == "full"
    assert set_subscription_import_scope(config, "pending").subscription_import_scope == "pending"


def test_cookie_poll_and_persistence(config: RagConfig) -> None:
    state = CookieLoginState(status="waiting", started_at=datetime.now().isoformat(timespec="seconds"))
    result = BrowserCookieResult(cookie="a=1; b=2", browser="test", profile="test", count=2)
    with patch("customer_rag.cookie_login.read_tencent_docs_cookie_from_login_window", return_value=result), patch(
        "customer_rag.cookie_login.close_tencent_docs_login_window"
    ):
        _poll_cookie(config, state, timeout_seconds=2, poll_seconds=1)
    assert state.status == "completed", state
    assert load_saved_cookie(config) == result.cookie


def test_web_cookie_two_click_flow(config: RagConfig) -> None:
    result = BrowserCookieResult(cookie="web=ok", browser="test", profile="test", count=1)
    with patch("customer_rag.cookie_login.is_tencent_docs_login_window_open", return_value=False), patch(
        "customer_rag.cookie_login.open_tencent_docs_login_window"
    ) as opened:
        state = open_cookie_login(config)
        assert state.status == "window_open"
        opened.assert_called_once()
    with patch("customer_rag.cookie_login.read_tencent_docs_cookie_from_login_window", return_value=result), patch(
        "customer_rag.cookie_login.close_tencent_docs_login_window"
    ):
        state = capture_cookie(config)
    assert state.status == "completed", state
    assert load_saved_cookie(config) == "web=ok"


def test_complete_subscription_flow(config: RagConfig) -> None:
    subscription = TencentDocSubscription(name="测试订阅", url="https://docs.qq.com/test", tags=["测试"])
    subscriptions_path = config.index_dir / "tencent_doc_subscriptions.json"
    save_subscriptions(subscriptions_path, [subscription])
    downloaded = config.raw_data_dir / "tencent_docs" / "测试订阅.xlsx"
    downloaded.parent.mkdir(parents=True, exist_ok=True)

    class FakePipeline:
        def __init__(self, _config):
            pass

        def replace_files_with_tags(self, path_tags, rebuild_index=False):
            assert path_tags == [(downloaded, ["测试"])]
            assert rebuild_index is False
            return {"documents": 3, "items": 4, "removed": 2}

        def rebuild_index(self, progress_callback=None):
            if progress_callback:
                progress_callback(100, "测试索引完成")
            return 7

    def fake_download(*args, progress_callback=None, **kwargs):
        downloaded.write_bytes(b"test")
        if progress_callback:
            progress_callback(4, 4)
        return downloaded

    set_auto_enabled(config, True, 20)
    with patch("customer_rag.subscription_jobs.fetch_subscription_page", return_value=object()), patch(
        "customer_rag.subscription_jobs.fetch_subscription_last_modified", return_value="2026-06-11T12:00:00+08:00"
    ), patch("customer_rag.subscription_jobs.download_subscription", side_effect=fake_download), patch(
        "customer_rag.pipeline.RagPipeline", FakePipeline
    ):
        started = start_subscription_job(config, subscriptions_path, [subscription], "cookie=ok", origin="auto")
        assert started.status == "running"
        wait_until(lambda: not is_subscription_worker_alive())

    state = read_job_state(config)
    assert state.status == "completed"
    assert state.current_index == state.total == 1
    assert state.updated_names == ["测试订阅"]
    assert state.documents == 3 and state.items == 4 and state.removed == 2 and state.chunks == 7
    assert state.duration_seconds >= 0
    coordinator = read_state(config)
    assert coordinator.active_kind == ""
    assert datetime.fromisoformat(coordinator.next_auto_at) > datetime.now() + timedelta(minutes=19)


def test_download_failure_requests_cookie(config: RagConfig) -> None:
    subscription = TencentDocSubscription(name="失败订阅", url="https://docs.qq.com/fail")
    subscriptions_path = config.index_dir / "tencent_doc_subscriptions.json"
    save_subscriptions(subscriptions_path, [subscription])
    downloaded = config.raw_data_dir / "tencent_docs" / "失败订阅.xlsx"
    downloaded.parent.mkdir(parents=True, exist_ok=True)

    class FakePipeline:
        def __init__(self, _config):
            pass

        def replace_files_with_tags(self, path_tags, rebuild_index=False):
            return {"documents": 1, "items": 1, "removed": 0}

        def rebuild_index(self, progress_callback=None):
            return 1

    def fake_download(*args, **kwargs):
        downloaded.write_bytes(b"test")
        return downloaded

    with patch("customer_rag.subscription_jobs.fetch_subscription_page", side_effect=[RuntimeError("Cookie 已失效"), object()]), patch(
        "customer_rag.subscription_jobs.fetch_subscription_last_modified", return_value="2026-06-11T12:00:00+08:00"
    ), patch("customer_rag.subscription_jobs.download_subscription", side_effect=fake_download), patch(
        "customer_rag.subscription_jobs.start_cookie_login"
    ) as login, patch(
        "customer_rag.subscription_jobs.read_login_state", return_value=CookieLoginState(status="completed")
    ), patch("customer_rag.subscription_jobs.load_saved_cookie", return_value="fresh-cookie"), patch(
        "customer_rag.pipeline.RagPipeline", FakePipeline
    ):
        started = start_subscription_job(config, subscriptions_path, [subscription], "bad-cookie", origin="auto")
        assert started.status == "running"
        wait_until(lambda: not is_subscription_worker_alive())
    state = read_job_state(config)
    assert state.status == "completed", state
    assert not state.cookie_refresh_required and state.failed == 0
    login.assert_called_once()


def test_timeout_does_not_restart_successful_downloads(config: RagConfig) -> None:
    good = TencentDocSubscription(name="正常订阅", url="https://docs.qq.com/good", tags=["测试"])
    slow = TencentDocSubscription(name="超时订阅", url="https://docs.qq.com/slow", tags=["测试"])
    subscriptions_path = config.index_dir / "tencent_doc_subscriptions.json"
    save_subscriptions(subscriptions_path, [good, slow])
    downloaded = config.raw_data_dir / "tencent_docs" / "正常订阅.xlsx"
    downloaded.parent.mkdir(parents=True, exist_ok=True)

    class FakePipeline:
        def __init__(self, _config):
            pass

        def replace_files_with_tags(self, path_tags, rebuild_index=False):
            assert path_tags == [(downloaded, ["测试"])]
            return {"documents": 1, "items": 1, "removed": 0}

        def rebuild_index(self, progress_callback=None):
            return 1

    def fake_page(subscription, _cookie):
        if subscription.url.endswith("slow"):
            raise TimeoutError("The read operation timed out")
        return object()

    def fake_download(*args, **kwargs):
        downloaded.write_bytes(b"test")
        return downloaded

    set_auto_enabled(config, True, 20)
    with patch("customer_rag.subscription_jobs.fetch_subscription_page", side_effect=fake_page), patch(
        "customer_rag.subscription_jobs.fetch_subscription_last_modified", return_value="2026-06-11T12:00:00+08:00"
    ), patch("customer_rag.subscription_jobs.download_subscription", side_effect=fake_download) as download, patch(
        "customer_rag.subscription_jobs.start_cookie_login"
    ) as login, patch("customer_rag.pipeline.RagPipeline", FakePipeline):
        started = start_subscription_job(config, subscriptions_path, [good, slow], "cookie=ok", origin="auto")
        assert started.status == "running"
        wait_until(lambda: not is_subscription_worker_alive())

    state = read_job_state(config)
    assert state.status == "completed", state
    assert state.downloaded == 1 and state.failed == 1
    assert not state.cookie_refresh_required
    assert download.call_count == 1
    login.assert_not_called()
    coordinator = read_state(config)
    assert datetime.fromisoformat(coordinator.next_auto_at) > datetime.now() + timedelta(minutes=19)


def test_merged_import_job(config: RagConfig) -> None:
    class FakePipeline:
        def __init__(self, _config):
            pass

        def rebuild_corpus_from_raw(self, **kwargs):
            callback = kwargs.get("progress_callback")
            if callback:
                callback(50, "解析完成")
            return {"documents": 2, "items": 3, "chunks": 0, "index_error": "", "parsed_files": 1, "reused_files": 0}

        def rebuild_index(self, progress_callback=None):
            if progress_callback:
                progress_callback(100, "索引完成")
            return 5

    set_auto_enabled(config, True, 20)
    with patch("customer_rag.raw_jobs.RagPipeline", FakePipeline), patch(
        "customer_rag.raw_jobs._commit_pending_subscription_updates", return_value=0
    ):
        state = start_raw_job(config, "import_data", scope="all")
        assert state.status == "running"
        wait_until(lambda: read_raw_job_state(config).status != "running")
    finished = read_raw_job_state(config)
    assert finished.status == "completed" and finished.chunks == 5
    assert datetime.fromisoformat(read_state(config).next_auto_at) > datetime.now() + timedelta(minutes=19)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tests = [
            test_scheduler_rules,
            test_incomplete_xlsx_does_not_replace_existing,
            test_manual_reschedules_auto,
            test_scheduler_init_is_idempotent,
            test_coordinator_write_retries_windows_file_lock,
            test_subscription_import_scope,
            test_cookie_poll_and_persistence,
            test_web_cookie_two_click_flow,
            test_complete_subscription_flow,
            test_download_failure_requests_cookie,
            test_timeout_does_not_restart_successful_downloads,
            test_merged_import_job,
        ]
        for index, test in enumerate(tests):
            config = make_config(root / str(index))
            test(config)
            print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
