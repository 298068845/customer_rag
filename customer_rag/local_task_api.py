from __future__ import annotations

import json
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from customer_rag.config import load_config
from customer_rag.corpus import CorpusStore
from customer_rag.auto_update import ensure_auto_update_scheduler
from customer_rag.logging_config import configure_logging, log_exception
from customer_rag.pipeline import RagPipeline
from customer_rag.cookie_login import (
    capture_cookie,
    cookie_window_is_open,
    has_saved_cookie,
    open_cookie_login,
    read_login_state,
)
from customer_rag.raw_jobs import recover_interrupted_raw_job, resume_interrupted_raw_job, start_raw_job
from customer_rag.subscription_jobs import (
    read_job_state,
    recover_interrupted_subscription_job,
    request_stop_subscription_job,
    resume_interrupted_subscription_job,
)
from customer_rag.task_coordinator import (
    mark_stale_idle,
    read_state as read_coordinator_state,
    set_auto_enabled,
    set_subscription_import_scope,
)
from customer_rag.tencent_docs import TencentDocSubscription, load_subscriptions, save_subscriptions
from customer_rag.talk_rag import sync_subscription_brand_replies


_server: ThreadingHTTPServer | None = None
_server_port: int | None = None
_lock = threading.Lock()
_qa_pipeline: RagPipeline | None = None
_qa_pipeline_config = None
_qa_pipeline_lock = threading.Lock()


def ensure_local_task_api(port: int = 8512) -> str:
    global _server, _server_port
    with _lock:
        if _server is not None:
            return f"http://127.0.0.1:{_server_port or port}"
        configure_logging()
        last_error: OSError | None = None
        for candidate_port in range(port, port + 10):
            try:
                _server = ThreadingHTTPServer(("127.0.0.1", candidate_port), _TaskApiHandler)
                _server_port = candidate_port
                break
            except OSError as exc:
                last_error = exc
        if _server is None:
            raise last_error or OSError("无法启动本地任务 API")
        threading.Thread(target=_server.serve_forever, daemon=True).start()
        config = load_config()
        recover_interrupted_raw_job(config)
        before_recovery = read_job_state(config)
        recovered = recover_interrupted_subscription_job(config)
        interrupted = before_recovery.status in {"running", "waiting_cookie", "rebuilding", "stopping"} and recovered.status == "error"
        resumed = resume_interrupted_subscription_job(config)
        resume_started = resumed.status == "running" and bool(resumed.job_id)
        if interrupted and not resume_started:
            mark_stale_idle(config)
        resume_interrupted_raw_job(config)
        ensure_auto_update_scheduler(config, run_immediately=not resume_started)
        return f"http://127.0.0.1:{_server_port or port}"


class _TaskApiHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature.
        return

    def do_OPTIONS(self) -> None:
        self._send_json({})

    def do_GET(self) -> None:
        try:
            self._do_GET()
        except Exception as exc:  # noqa: BLE001 - local API must return JSON diagnostics.
            log_exception(
                "app",
                "local_task_api_error",
                "local task API request failed",
                exc,
                context={"path": self.path, "client": str(self.client_address)},
            )
            self._send_json({"ok": False, "error": str(exc), "error_type": type(exc).__name__}, status=500)

    def _do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        config = load_config()
        if parsed.path == "/raw/start":
            task = query.get("task", [""])[0]
            scope = query.get("scope", ["all"])[0]
            self._send_json(asdict(start_raw_job(config, task, scope=scope)))
            return
        if parsed.path == "/raw/status":
            self._send_json(asdict(recover_interrupted_raw_job(config)))
            return
        if parsed.path == "/subscription/status":
            payload = asdict(recover_interrupted_subscription_job(config))
            payload["coordinator"] = asdict(read_coordinator_state(config))
            payload["cookie_saved"] = has_saved_cookie(config)
            payload["cookie_login"] = asdict(read_login_state(config))
            payload["cookie_window_open"] = cookie_window_is_open()
            self._send_json(payload)
            return
        if parsed.path == "/subscription/stop":
            request_stop_subscription_job(config)
            self._send_json(asdict(read_job_state(config)))
            return
        if parsed.path == "/auto/status":
            self._send_json(asdict(read_coordinator_state(config)))
            return
        if parsed.path == "/auto/set":
            enabled = query.get("enabled", ["0"])[0] in {"1", "true", "yes"}
            interval = int(query.get("interval", ["20"])[0])
            self._send_json(asdict(set_auto_enabled(config, enabled, interval)))
            return
        if parsed.path == "/subscription/import-scope":
            scope = query.get("scope", ["pending"])[0]
            self._send_json(asdict(set_subscription_import_scope(config, scope)))
            return
        if parsed.path == "/subscriptions/list":
            self._send_json(
                {
                    "subscriptions": [
                        asdict(subscription)
                        for subscription in load_subscriptions(config.index_dir / "tencent_doc_subscriptions.json")
                    ]
                }
            )
            return
        if parsed.path == "/subscriptions/set-enabled":
            url = query.get("url", [""])[0].strip()
            enabled = query.get("enabled", ["0"])[0] in {"1", "true", "yes"}
            self._send_json(self._update_subscription_enabled(config, url, enabled))
            return
        if parsed.path == "/subscriptions/select-all":
            enabled = query.get("enabled", ["0"])[0] in {"1", "true", "yes"}
            path = config.index_dir / "tencent_doc_subscriptions.json"
            subscriptions = [
                TencentDocSubscription(
                    name=item.name,
                    url=item.url,
                    tags=item.tags,
                    enabled=enabled,
                    last_updated=item.last_updated,
                    last_status=item.last_status,
                    last_modified=item.last_modified,
                )
                for item in load_subscriptions(path)
            ]
            save_subscriptions(path, subscriptions)
            sync_subscription_brand_replies(config)
            self._send_json({"ok": True, "updated": len(subscriptions)})
            return
        if parsed.path == "/subscriptions/set-delete-selected":
            url = query.get("url", [""])[0].strip()
            selected = query.get("selected", ["0"])[0] in {"1", "true", "yes"}
            self._send_json(self._update_subscription_delete_selection(config, url, selected))
            return
        if parsed.path == "/subscriptions/select-delete-all":
            selected = query.get("selected", ["0"])[0] in {"1", "true", "yes"}
            self._send_json(self._update_all_subscription_delete_selection(config, selected))
            return
        if parsed.path == "/subscriptions/delete-selection":
            self._send_json({"urls": sorted(_load_subscription_delete_selection(config))})
            return
        if parsed.path == "/subscriptions/update":
            self._send_json(self._update_subscription(config, query))
            return
        if parsed.path == "/cookie/login/open":
            self._send_json(asdict(open_cookie_login(config)))
            return
        if parsed.path == "/cookie/login/read":
            self._send_json(asdict(capture_cookie(config)))
            return
        if parsed.path == "/cookie/status":
            payload = asdict(read_login_state(config))
            payload["saved"] = has_saved_cookie(config)
            self._send_json(payload)
            return
        if parsed.path == "/qa/ask":
            question = query.get("question", [""])[0].strip()
            if not question:
                self._send_json({"error": "请输入问题"}, status=400)
                return
            self._send_json(_qa_payload(config, question, query.get("tag", [])))
            return
        if parsed.path == "/corpus/search":
            self._send_json(_corpus_search_payload(config, query))
            return
        self._send_json({"error": "not found"}, status=404)

    def _update_subscription_enabled(self, config, url: str, enabled: bool) -> dict:
        path = config.index_dir / "tencent_doc_subscriptions.json"
        subscriptions = load_subscriptions(path)
        updated: list[TencentDocSubscription] = []
        changed = False
        for item in subscriptions:
            if item.url == url:
                changed = True
                updated.append(
                    TencentDocSubscription(
                        name=item.name,
                        url=item.url,
                        tags=item.tags,
                        enabled=enabled,
                        last_updated=item.last_updated,
                        last_status=item.last_status,
                        last_modified=item.last_modified,
                    )
                )
            else:
                updated.append(item)
        if changed:
            save_subscriptions(path, updated)
            sync_subscription_brand_replies(config)
        return {"ok": changed}

    def _update_subscription_delete_selection(self, config, url: str, selected: bool) -> dict:
        if not url:
            return {"ok": False}
        urls = _load_subscription_delete_selection(config)
        if selected:
            urls.add(url)
        else:
            urls.discard(url)
        _save_subscription_delete_selection(config, urls)
        return {"ok": True, "selected": sorted(urls)}

    def _update_all_subscription_delete_selection(self, config, selected: bool) -> dict:
        if selected:
            urls = {item.url for item in load_subscriptions(config.index_dir / "tencent_doc_subscriptions.json")}
        else:
            urls = set()
        _save_subscription_delete_selection(config, urls)
        return {"ok": True, "selected": sorted(urls)}

    def _update_subscription(self, config, query: dict[str, list[str]]) -> dict:
        original_url = query.get("url", [""])[0].strip()
        name = query.get("name", [""])[0].strip()
        next_url = query.get("next_url", [""])[0].strip()
        tags = _parse_tags(query.get("tags", [""])[0])
        enabled = query.get("enabled", ["1"])[0] in {"1", "true", "yes"}
        if not name or not next_url:
            return {"ok": False, "error": "订阅必须填写名称和腾讯文档地址"}
        path = config.index_dir / "tencent_doc_subscriptions.json"
        subscriptions = load_subscriptions(path)
        updated: list[TencentDocSubscription] = []
        changed = False
        for item in subscriptions:
            if item.url == original_url:
                changed = True
                url_changed = item.url != next_url
                updated.append(
                    TencentDocSubscription(
                        name=name,
                        url=next_url,
                        tags=tags,
                        enabled=enabled,
                        last_updated=item.last_updated,
                        last_status="" if url_changed else item.last_status,
                        last_modified="" if url_changed else item.last_modified,
                    )
                )
            else:
                updated.append(item)
        if changed:
            save_subscriptions(path, updated)
            sync_subscription_brand_replies(config)
        return {"ok": changed}

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _get_qa_pipeline(config) -> RagPipeline:
    """Reuse the web-query pipeline while allowing configuration changes."""
    global _qa_pipeline, _qa_pipeline_config
    with _qa_pipeline_lock:
        if _qa_pipeline is None or _qa_pipeline_config != config:
            _qa_pipeline = RagPipeline(config)
            _qa_pipeline_config = config
        return _qa_pipeline


def _qa_payload(config, question: str, tags: list[str]) -> dict:
    result = _get_qa_pipeline(config).ask(question, tags=[tag for tag in tags if tag])
    return {
        "answer": result.answer,
        "warning": "检索超时，已返回当前最相关的结果。" if result.timed_out else "",
        "fallback": result.fallback,
        "timed_out": result.timed_out,
        "sources": [
            {
                "title": source.title,
                "source": source.source,
                "location": source.location,
                "score": source.score,
                "text": source.text,
                "tags": source.tags,
            }
            for source in result.sources
        ],
    }


def _corpus_search_payload(config, query: dict[str, list[str]]) -> dict:
    keyword = query.get("keyword", [""])[0].strip().lower()
    selected_sources = {value for value in query.get("source", []) if value}
    selected_tags = {value for value in query.get("tag", []) if value}
    image_filter = query.get("image", ["全部"])[0]
    sort_mode = query.get("sort", ["更新时间倒序"])[0]
    try:
        limit = max(1, min(int(query.get("limit", ["50"])[0]), 200))
    except ValueError:
        limit = 50

    items = CorpusStore(config.index_dir / "corpus.jsonl").list_items()
    matched = []
    for item in items:
        searchable = "\n".join([item.title, item.source, item.location, item.text, " ".join(item.tags)]).lower()
        if keyword and keyword not in searchable:
            continue
        if selected_sources and item.source not in selected_sources:
            continue
        if selected_tags and not selected_tags.intersection(item.tags):
            continue
        if image_filter == "有图片" and not item.image_paths:
            continue
        if image_filter == "无图片" and item.image_paths:
            continue
        matched.append(item)

    if sort_mode == "创建时间倒序":
        matched.sort(key=lambda item: item.created_at, reverse=True)
    elif sort_mode == "标题 A-Z":
        matched.sort(key=lambda item: item.title.casefold())
    else:
        matched.sort(key=lambda item: item.updated_at, reverse=True)

    return {
        "total": len(items),
        "matched": len(matched),
        "rows": [
            {
                "title": item.title,
                "source": item.source,
                "tags": item.tags,
                "summary": item.text.replace("\n", " ")[:280],
                "image_count": len(item.image_paths),
                "updated_at": item.updated_at,
            }
            for item in matched[:limit]
        ],
    }


def _parse_tags(value: str) -> list[str]:
    return [tag.strip() for tag in value.replace("，", ",").split(",") if tag.strip()]


def _subscription_delete_selection_path(config):
    return config.index_dir / "subscription_delete_selection.json"


def _load_subscription_delete_selection(config) -> set[str]:
    path = _subscription_delete_selection_path(config)
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return set()
    if not isinstance(payload, list):
        return set()
    return {str(url).strip() for url in payload if str(url).strip()}


def _save_subscription_delete_selection(config, urls: set[str]) -> None:
    path = _subscription_delete_selection_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(urls), ensure_ascii=False, indent=2), encoding="utf-8")
