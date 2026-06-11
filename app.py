from __future__ import annotations

import json
import html
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import customer_rag.browser_cookies as browser_cookies
import customer_rag.corpus as corpus_module
import customer_rag.pipeline as pipeline_module
import customer_rag.subscription_jobs as subscription_jobs_module
from customer_rag.config import load_config, save_machine_config
from customer_rag.cookie_login import has_saved_cookie, load_saved_cookie, start_cookie_login
from customer_rag.llama_server import build_llama_server_plan, is_llama_server_healthy
from customer_rag.task_coordinator import activity, read_state as read_coordinator_state
from uuid import uuid4

from customer_rag.category_config import category_aliases, category_brands, save_category_catalog
from customer_rag.local_task_api import ensure_local_task_api
from customer_rag.loaders import SUPPORTED_SUFFIXES
from customer_rag.prompt_defaults import DEFAULT_SYSTEM_PROMPT
from customer_rag.time_format import display_datetime
RagPipeline = pipeline_module.RagPipeline
is_subscription_job_running = subscription_jobs_module.is_subscription_job_running
read_job_state = subscription_jobs_module.read_job_state
request_stop_subscription_job = subscription_jobs_module.request_stop_subscription_job
start_subscription_job = subscription_jobs_module.start_subscription_job
from customer_rag.tencent_docs import (
    TencentDocSubscription,
    load_subscriptions,
    save_subscriptions,
    subscription_output_path,
)


st.set_page_config(page_title="本地腾讯文档 RAG", layout="wide")
LOCAL_TASK_API_URL = ensure_local_task_api()
st.markdown(
    """
    <style>
    .corpus-toolbar-style + div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-of-type(1) button {
        background: #e8f3ff;
        border-color: #9ecbff;
        color: #075eb8;
    }
    .corpus-toolbar-style + div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-of-type(8) button {
        background: #ff4b4b;
        border-color: #ff4b4b;
        color: #ffffff;
    }
    .corpus-toolbar-style + div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-of-type(8) button:hover {
        background: #e53e3e;
        border-color: #e53e3e;
        color: #ffffff;
    }
    div[role="dialog"] {
        position: fixed !important;
        top: 50% !important;
        left: 50% !important;
        transform: translate(-50%, -50%) !important;
        margin: 0 !important;
    }
    .import-action-style + div[data-testid="stHorizontalBlock"] button,
    .upload-action-style + div[data-testid="stHorizontalBlock"] button,
    .raw-action-style + div[data-testid="stHorizontalBlock"] button {
        border-radius: 7px;
        color: #fff;
        font-weight: 650;
        box-shadow: 0 2px 0 rgba(0, 0, 0, 0.10), 0 6px 12px rgba(25, 83, 157, 0.12);
    }
    .import-action-style + div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-of-type(1) button {
        background: #e04b4b;
        border-color: #bf3535;
    }
    .import-action-style + div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-of-type(2) button,
    .upload-action-style + div[data-testid="stHorizontalBlock"] button,
    .raw-action-style + div[data-testid="stHorizontalBlock"] button {
        background: #2f7de1;
        border-color: #1d64bb;
    }
    .import-action-style + div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-of-type(3) button {
        background: #2fa66a;
        border-color: #218654;
    }
    .import-action-style + div[data-testid="stHorizontalBlock"] button:hover,
    .upload-action-style + div[data-testid="stHorizontalBlock"] button:hover,
    .raw-action-style + div[data-testid="stHorizontalBlock"] button:hover {
        filter: brightness(0.96);
        color: #fff;
    }
    .st-key-subscription_start button,
    .st-key-subscription_add button,
    .st-key-subscription_save button,
    .st-key-login_tencent_docs button,
    .st-key-upload_import button,
    .st-key-import_rebuild_raw_v2 button,
    .st-key-import_rebuild_index_v2 button {
        border-radius: 8px !important;
        color: #fff !important;
        font-weight: 650 !important;
        height: 40px !important;
        border-width: 1px !important;
        background-image: none !important;
        box-shadow: 0 2px 0 rgba(0, 0, 0, 0.10), 0 6px 12px rgba(25, 83, 157, 0.12) !important;
        transform: translateY(0);
    }
    .st-key-subscription_start button {
        background: #e04b4b !important;
        border-color: #bf3535 !important;
    }
    .st-key-subscription_add button,
    .st-key-login_tencent_docs button,
    .st-key-upload_import button,
    .st-key-import_rebuild_raw_v2 button,
    .st-key-import_rebuild_index_v2 button {
        background: #2f7de1 !important;
        border-color: #1d64bb !important;
    }
    .st-key-subscription_save button {
        background: #2fa66a !important;
        border-color: #218654 !important;
    }
    .st-key-subscription_start button:hover,
    .st-key-subscription_add button:hover,
    .st-key-subscription_save button:hover,
    .st-key-login_tencent_docs button:hover,
    .st-key-upload_import button:hover,
    .st-key-import_rebuild_raw_v2 button:hover,
    .st-key-import_rebuild_index_v2 button:hover {
        color: #fff !important;
        filter: brightness(0.96);
    }
    .import-title {
        font-size: 2rem;
        line-height: 2.5rem;
        font-weight: 750;
        margin: 0.12rem 0 0.9rem 0;
    }
    .cookie-pill {
        display: inline-flex;
        align-items: center;
        height: 40px;
        margin-top: 0.1rem;
        font-weight: 700;
        color: #2f3340;
        white-space: nowrap;
    }
    .upload-tag-spacer {
        height: 1.58rem;
    }
    .upload-tag-row {
        height: 0;
        min-height: 0;
        overflow: hidden;
        margin: 0;
        padding: 0;
    }
    .upload-tag-row + div[data-testid="stHorizontalBlock"] {
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        align-items: stretch !important;
        gap: 1rem;
    }
    .upload-tag-row + div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
        display: flex;
        flex-direction: column;
        justify-content: flex-end;
        align-self: stretch !important;
        min-width: 0 !important;
    }
    .upload-tag-row + div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-of-type(1) {
        flex: 1 1 auto !important;
        width: auto !important;
    }
    .upload-tag-row + div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-of-type(2) {
        flex: 0 0 220px !important;
        width: 220px !important;
    }
    .upload-tag-row + div[data-testid="stHorizontalBlock"] [data-testid="stTextInput"] {
        margin-bottom: 0;
    }
    .upload-tag-row + div[data-testid="stHorizontalBlock"] .st-key-upload_import {
        margin-top: 2.35rem !important;
        margin-bottom: 0;
    }
    div[data-testid="stElementContainer"]:has(.upload-tag-row) + div[data-testid="stLayoutWrapper"] div[data-testid="stHorizontalBlock"] {
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        align-items: stretch !important;
        gap: 1rem;
    }
    div[data-testid="stElementContainer"]:has(.upload-tag-row) + div[data-testid="stLayoutWrapper"] div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
        display: flex;
        flex-direction: column;
        justify-content: flex-end;
        align-self: stretch !important;
        min-width: 0 !important;
    }
    div[data-testid="stElementContainer"]:has(.upload-tag-row) + div[data-testid="stLayoutWrapper"] div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-of-type(1) {
        flex: 1 1 auto !important;
        width: auto !important;
    }
    div[data-testid="stElementContainer"]:has(.upload-tag-row) + div[data-testid="stLayoutWrapper"] div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-of-type(2) {
        flex: 0 0 220px !important;
        width: 220px !important;
    }
    div[data-testid="stElementContainer"]:has(.upload-tag-row) + div[data-testid="stLayoutWrapper"] .st-key-upload_import {
        margin-top: 2.35rem !important;
        margin-bottom: 0;
    }
    @media (max-width: 1200px) {
        .import-title {
            font-size: 1.65rem;
            line-height: 2.1rem;
        }
        .cookie-pill {
            font-size: 0.92rem;
        }
        .st-key-subscription_start button,
        .st-key-subscription_add button,
        .st-key-subscription_save button,
        .st-key-login_tencent_docs button,
        .st-key-upload_import button {
            font-size: 0.9rem !important;
            padding-left: 0.45rem !important;
            padding-right: 0.45rem !important;
        }
        .upload-tag-row + div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-of-type(2) {
            flex-basis: 170px !important;
            width: 170px !important;
        }
        div[data-testid="stElementContainer"]:has(.upload-tag-row) + div[data-testid="stLayoutWrapper"] div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-of-type(2) {
            flex-basis: 170px !important;
            width: 170px !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def queue_ui_notice(level: str, message: str) -> None:
    renderer = getattr(st, level, st.info)
    renderer(message)


def render_ui_notice() -> None:
    notice = st.session_state.pop("ui_notice", None)
    if not isinstance(notice, dict):
        return
    level = str(notice.get("level", "info"))
    message = str(notice.get("message", ""))
    if not message:
        return
    renderer = getattr(st, level, st.info)
    renderer(message)


@st.cache_resource
def get_pipeline(cache_version: str = "llama-server-v1") -> RagPipeline:
    return RagPipeline(load_config())


@st.cache_data(show_spinner=False)
def cached_corpus_item_payloads(signature: tuple[int, int]) -> list[dict]:
    return [asdict(item) for item in get_pipeline().list_corpus()]


def cached_corpus_items(signature: tuple[int, int]) -> list:
    return [corpus_module.CorpusItem(**payload) for payload in cached_corpus_item_payloads(signature)]


def corpus_signature() -> tuple[int, int]:
    path = cfg.index_dir / "corpus.jsonl"
    if not path.exists():
        return (0, 0)
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def subscription_pipeline() -> RagPipeline:
    current = get_pipeline()
    if hasattr(current, "replace_files_with_tags"):
        return current
    st.cache_resource.clear()
    return RagPipeline(load_config())


def save_uploaded_files(uploaded_files: list) -> list[Path]:
    raw_dir = cfg.raw_data_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[Path] = []
    for uploaded_file in uploaded_files:
        safe_name = Path(uploaded_file.name).name
        target = raw_dir / safe_name
        target.write_bytes(uploaded_file.getbuffer())
        saved_paths.append(target)
    return saved_paths


def remember_recent_raw_files(paths: list[Path]) -> None:
    st.session_state["recent_raw_files"] = [str(path.resolve()) for path in paths]


def recent_raw_files_from_state() -> list[str]:
    job_state = read_job_state(cfg)
    if not (job_state.updated_files or []):
        return []
    files: list[str] = []
    for path in job_state.updated_files or []:
        if path not in files:
            files.append(path)
    return files


def latest_subscription_modified(subscriptions: list[TencentDocSubscription] | None = None) -> str:
    items = subscriptions if subscriptions is not None else load_subscriptions(subscriptions_path())
    values = [str(subscription.last_modified or "") for subscription in items if str(subscription.last_modified or "")]
    return max(values) if values else ""


def subscription_last_modified_by_file() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for subscription in load_subscriptions(subscriptions_path()):
        output_path = subscription_output_path(subscription, cfg.raw_data_dir)
        modified = str(subscription.last_modified or "")
        for key in _raw_file_keys(output_path):
            mapping[key] = modified
    return mapping


def is_recent_raw_file(path: Path, recent_files: list[str]) -> bool:
    if not recent_files:
        return False
    candidates = _raw_file_keys(path)
    return any(_normalize_path_key(value) in candidates for value in recent_files)


def _raw_file_keys(path: Path) -> set[str]:
    keys = {str(path), str(path.resolve()), path.name}
    try:
        keys.add(str(path.relative_to(cfg.raw_data_dir)))
    except ValueError:
        pass
    try:
        keys.add(str(path.resolve().relative_to(Path.cwd().resolve())))
    except ValueError:
        pass
    return {_normalize_path_key(value) for value in keys}


def _normalize_path_key(value: str | Path) -> str:
    return str(value).replace("\\", "/").lower()


def render_raw_files_table(raw_files: list[Path], recent_files: list[str]) -> None:
    if not raw_files:
        st.info("暂无原始文件。")
        return

    rows = []
    recent_count = 0
    remote_modified_by_file = subscription_last_modified_by_file()
    for path in raw_files:
        is_recent = is_recent_raw_file(path, recent_files)
        if is_recent:
            recent_count += 1
        remote_modified = remote_modified_by_file.get(_normalize_path_key(path))
        rows.append(
            {
                "name": str(path.relative_to(cfg.raw_data_dir)),
                "source": "腾讯文档" if "tencent_docs" in path.parts else "上传文件",
                "status": "已保存",
                "updated": display_datetime(remote_modified)
                or datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "recent": is_recent,
            }
        )

    table_rows = "\n".join(
        "<tr data-recent='{recent}'>"
        "<td>{name}</td><td>{source}</td><td>{status}</td><td>{updated}</td>"
        "</tr>".format(
            recent="1" if row["recent"] else "0",
            name=html.escape(row["name"]),
            source=html.escape(row["source"]),
            status=html.escape(row["status"]),
            updated=html.escape(row["updated"]),
        )
        for row in rows
    )
    component_id = "raw-file-table"
    components.html(
        f"""
        <style>
          .raw-filter-bar {{
            display: flex;
            align-items: center;
            gap: 12px;
            margin: 0 0 8px 0;
            color: #2f3440;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            font-size: 15px;
          }}
          .raw-filter-bar input {{
            width: 16px;
            height: 16px;
            accent-color: #4b7fda;
          }}
          .raw-filter-count {{
            color: #8a91a0;
            font-size: 13px;
          }}
          .raw-table-wrap {{
            height: 260px;
            overflow: auto;
            border: 1px solid #e6e9ef;
            border-radius: 8px;
            background: #fff;
          }}
          .raw-table {{
            border-collapse: collapse;
            width: 100%;
            min-width: 860px;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            font-size: 15px;
            color: #2f3440;
          }}
          .raw-table th {{
            position: sticky;
            top: 0;
            z-index: 1;
            background: #f7f8fb;
            color: #8a91a0;
            text-align: left;
            font-weight: 600;
            padding: 10px 12px;
            border-bottom: 1px solid #e6e9ef;
          }}
          .raw-table td {{
            padding: 10px 12px;
            border-bottom: 1px solid #edf0f4;
            white-space: nowrap;
          }}
          .raw-table td:first-child {{
            max-width: 520px;
            overflow: hidden;
            text-overflow: ellipsis;
          }}
          .raw-empty {{
            display: none;
            padding: 18px 12px;
            color: #8a91a0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          }}
        </style>
        <div id="{component_id}">
          <label class="raw-filter-bar">
            <input type="checkbox" data-role="recent-toggle">
            <span>仅显示待入库更新文件</span>
            <span class="raw-filter-count" data-role="count">全量文件 {len(rows)} 个</span>
          </label>
          <div class="raw-table-wrap">
            <table class="raw-table">
              <thead>
                <tr><th>文件名</th><th>来源</th><th>状态</th><th>最后修改</th></tr>
              </thead>
              <tbody>{table_rows}</tbody>
            </table>
            <div class="raw-empty" data-role="empty">当前没有已下载但尚未完成解析和重建索引的更新文件。</div>
          </div>
        </div>
        <script>
          (() => {{
            const root = document.getElementById("{component_id}");
            const toggle = root.querySelector('[data-role="recent-toggle"]');
            const rows = Array.from(root.querySelectorAll('tbody tr'));
            const count = root.querySelector('[data-role="count"]');
            const empty = root.querySelector('[data-role="empty"]');
            const total = rows.length;
            const recentTotal = {recent_count};
            function applyFilter() {{
              const recentOnly = toggle.checked;
              let visible = 0;
              rows.forEach((row) => {{
                const show = !recentOnly || row.dataset.recent === "1";
                row.style.display = show ? "" : "none";
                if (show) visible += 1;
              }});
              count.textContent = recentOnly
                ? `当前筛选：待入库更新文件 ${{visible}} 个`
                : `全量文件 ${{total}} 个`;
              empty.style.display = recentOnly && visible === 0 ? "block" : "none";
            }}
            toggle.addEventListener("change", applyFilter);
            applyFilter();
          }})();
        </script>
        """,
        height=306,
        scrolling=False,
    )


def render_raw_action_panel(api_url: str, pending_count: int = 0) -> None:
    component_id = "raw-actions"
    components.html(
        f"""
        <style>
          .raw-actions {{
            display: grid;
            grid-template-columns: minmax(180px, 0.22fr) minmax(190px, 0.2fr) minmax(150px, 0.17fr) 1fr;
            gap: 18px;
            align-items: center;
            margin: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          }}
          .raw-actions button {{
            display: flex;
            align-items: center;
            justify-content: center;
            height: 42px;
            border: 1px solid #1d64bb;
            border-radius: 8px;
            background: #2f7de1;
            color: #fff;
            font-weight: 650;
            box-shadow: 0 2px 0 rgba(0,0,0,.10), 0 6px 12px rgba(25,83,157,.12);
            cursor: pointer;
          }}
          .raw-actions button:disabled {{
            cursor: not-allowed;
            opacity: .58;
          }}
          .raw-actions button:hover:not(:disabled) {{
            filter: brightness(.96);
          }}
          .raw-status {{
            min-width: 0;
          }}
          .raw-scope {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            color: #5f6673;
            font-size: 14px;
            white-space: nowrap;
          }}
          .raw-scope input {{
            width: 16px;
            height: 16px;
            accent-color: #4b7fda;
          }}
          .raw-status-line {{
            color: #5f6673;
            font-size: 14px;
            margin-bottom: 8px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }}
          .raw-progress {{
            height: 8px;
            border-radius: 999px;
            background: #edf1f7;
            overflow: hidden;
          }}
          .raw-progress > div {{
            width: 0%;
            height: 100%;
            background: #2f7de1;
            transition: width .22s ease;
          }}
          @media (max-width: 900px) {{
            .raw-actions {{
              grid-template-columns: 1fr;
              gap: 10px;
            }}
          }}
        </style>
        <div id="{component_id}">
          <div class="raw-actions">
            <button data-task="import_data">导入数据</button>
            <label class="raw-scope">
              <input type="checkbox" data-role="pending-scope" {"disabled" if pending_count <= 0 else ""}>
              <span>仅解析待入库更新文件</span>
            </label>
            <label class="raw-scope">
              <input type="checkbox" data-role="full-scope">
              <span>强制全量解析</span>
            </label>
            <div class="raw-status">
              <div class="raw-status-line" data-role="line">空闲</div>
              <div class="raw-progress"><div data-role="bar"></div></div>
            </div>
          </div>
        </div>
        <script>
          (() => {{
            const api = "{api_url}";
            const root = document.getElementById("{component_id}");
            const buttons = Array.from(root.querySelectorAll("button[data-task]"));
            const pendingScope = root.querySelector('[data-role="pending-scope"]');
            const fullScope = root.querySelector('[data-role="full-scope"]');
            const line = root.querySelector('[data-role="line"]');
            const bar = root.querySelector('[data-role="bar"]');
            let polling = null;

            function setBusy(busy) {{
              buttons.forEach((button) => button.disabled = busy);
            }}
            function render(state) {{
              const percent = Math.max(0, Math.min(100, Number(state.percent || 0)));
              const message = state.message || state.error || "空闲";
              bar.style.width = `${{percent}}%`;
              line.textContent = state.status === "idle" ? "空闲" : `${{message}} · ${{percent}}%`;
              if (state.status === "running" || state.status === "busy") {{
                setBusy(true);
              }} else if (state.status === "completed") {{
                setBusy(false);
                const detail = state.chunks ? `${{message}}，索引 ${{state.chunks}} 个片段` : message;
                line.textContent = detail;
                bar.style.width = "100%";
                stopPolling();
                if (Number(state.committed_subscriptions || 0) > 0) {{
                  line.textContent = `${{detail}}，订阅列表修改时间已提交`;
                }}
              }} else if (state.status === "error") {{
                setBusy(false);
                line.textContent = state.error || message;
                stopPolling();
              }} else {{
                setBusy(false);
              }}
            }}
            async function fetchStatus() {{
              const response = await fetch(`${{api}}/raw/status`, {{ cache: "no-store" }});
              return await response.json();
            }}
            function startPolling() {{
              stopPolling();
              polling = window.setInterval(async () => {{
                try {{ render(await fetchStatus()); }}
                catch (error) {{ line.textContent = `状态读取失败：${{String(error)}}`; }}
              }}, 650);
            }}
            function stopPolling() {{
              if (polling) window.clearInterval(polling);
              polling = null;
            }}
            if (pendingScope && fullScope) {{
              pendingScope.addEventListener("change", () => {{
                if (pendingScope.checked) fullScope.checked = false;
              }});
              fullScope.addEventListener("change", () => {{
                if (fullScope.checked) pendingScope.checked = false;
              }});
            }}
            async function startTask(task) {{
              setBusy(true);
              line.textContent = "正在启动...";
              const scope = task === "import_data"
                ? (pendingScope && pendingScope.checked ? "pending" : (fullScope && fullScope.checked ? "full" : "all"))
                : "all";
              const response = await fetch(`${{api}}/raw/start?task=${{encodeURIComponent(task)}}&scope=${{encodeURIComponent(scope)}}`, {{ cache: "no-store" }});
              const state = await response.json();
              render(state);
              if (state.status === "running") startPolling();
            }}
            buttons.forEach((button) => {{
              button.addEventListener("click", () => startTask(button.dataset.task).catch((error) => {{
                setBusy(false);
                line.textContent = `任务启动失败：${{String(error)}}`;
              }}));
            }});
            fetchStatus().then((state) => {{
              render(state);
              if (state.status === "running") startPolling();
            }}).catch(() => {{}});
            async function refreshCoordinator() {{
              try {{
                const response = await fetch(`${{api}}/auto/status`, {{cache:"no-store"}});
                const state = await response.json();
                setBusy(Boolean(state.active_kind));
              }} catch (error) {{}}
            }}
            refreshCoordinator();
            window.setInterval(refreshCoordinator, 1500);
          }})();
        </script>
        """,
        height=92,
        scrolling=False,
    )


def render_subscription_header(api_url: str) -> None:
    components.html(
        f"""
        <style>
          body {{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#202b42}}
          .header {{display:flex;flex-direction:column;gap:12px;width:100%;padding:2px 0;box-sizing:border-box}}
          .header-main {{display:flex;align-items:center;justify-content:space-between;gap:16px;min-width:0}}
          .auth {{display:flex;align-items:center;justify-content:flex-end;gap:14px;min-width:0}}
          .settings {{display:flex;align-items:center;flex-wrap:wrap;column-gap:22px;row-gap:10px;min-height:24px}}
          .schedule {{min-height:18px;color:#687181;font-size:13px}}
          .title {{font-size:24px;font-weight:750}}
          .auto {{display:inline-flex;align-items:center;gap:8px;white-space:nowrap;font-size:14px;color:#4e5665}}
          .auto input {{width:17px;height:17px;accent-color:#2f7de1}}
          button {{height:40px;min-width:150px;padding:0 18px;border:1px solid #1d64bb;border-radius:8px;background:#2f7de1;color:#fff;font-weight:650;cursor:pointer;white-space:nowrap}}
          button:disabled {{opacity:.58;cursor:not-allowed}}
          .pill {{white-space:nowrap;font-weight:650;font-size:14px}}
          .hint {{min-height:16px;text-align:right;font-size:12px;color:#687181}}
          @media (max-width: 720px) {{
            .header-main {{align-items:flex-start;flex-direction:column}}
            .auth {{justify-content:flex-start;flex-wrap:wrap}}
          }}
        </style>
        <div class="header">
          <div class="header-main">
            <div class="title">订阅获取</div>
            <div class="auth">
              <button data-role="login">登录腾讯文档</button>
              <div class="pill" data-role="cookie">Cookie：读取中...</div>
            </div>
          </div>
          <div class="settings">
            <label class="auto"><input type="checkbox" data-role="pending-scope"><span>仅解析待入库更新文件</span></label>
            <label class="auto"><input type="checkbox" data-role="full-scope"><span>强制全量解析</span></label>
            <label class="auto"><input type="checkbox" data-role="auto"><span>定时获取间隔：20分钟</span></label>
          </div>
          <div class="schedule" data-role="schedule"></div>
        </div>
        <div class="hint" data-role="hint"></div>
        <script>
          (() => {{
            const api="{api_url}";
            const auto=document.querySelector('[data-role="auto"]');
            const pendingScope=document.querySelector('[data-role="pending-scope"]');
            const fullScope=document.querySelector('[data-role="full-scope"]');
            const login=document.querySelector('[data-role="login"]');
            const cookie=document.querySelector('[data-role="cookie"]');
            const hint=document.querySelector('[data-role="hint"]');
            const schedule=document.querySelector('[data-role="schedule"]');
            let lastCookieSuccess="";
            let lastCookieError="";
            let nextAutoAt="";
            let autoEnabled=false;
            let cookieActionStarted=false;
            function pad(value) {{ return String(value).padStart(2,"0"); }}
            function updateCountdown() {{
              if(!autoEnabled) {{ schedule.textContent="定时获取未开启"; return; }}
              if(!nextAutoAt) {{ schedule.textContent="当前任务完成后开始下一轮倒计时"; return; }}
              const due=new Date(nextAutoAt);
              if(Number.isNaN(due.getTime())) {{ schedule.textContent=`下次自动更新：${{nextAutoAt}}`; return; }}
              const diff=Math.max(0, due.getTime()-Date.now());
              const totalSeconds=Math.ceil(diff/1000);
              const minutes=Math.floor(totalSeconds/60);
              const seconds=totalSeconds%60;
              const timeText=`${{pad(due.getHours())}}:${{pad(due.getMinutes())}}:${{pad(due.getSeconds())}}`;
              schedule.textContent=diff<=0
                ? `下次自动更新：${{timeText}}，即将开始`
                : `下次自动更新：${{timeText}}，倒计时 ${{minutes}}分${{pad(seconds)}}秒`;
            }}
            function setParentButtonsDisabled(disabled) {{
              try {{
                const labels=new Set(["开始后台更新","后台更新中...","增加订阅","保存订阅"]);
                window.parent.document.querySelectorAll('button').forEach((button)=>{{
                  if(labels.has((button.innerText||"").trim())) button.disabled=disabled;
                }});
              }} catch(error) {{}}
            }}
            function showToast(text,kind="success") {{
              let doc=document; try {{if(window.parent&&window.parent.document)doc=window.parent.document}} catch(error){{}}
              const id="customer-rag-cookie-toast"; const old=doc.getElementById(id); if(old)old.remove();
              const toast=doc.createElement("div"); toast.id=id; toast.textContent=text;
              toast.style.cssText=`position:fixed;right:24px;bottom:24px;z-index:999999;max-width:460px;padding:14px 18px;border-radius:10px;background:${{kind==='error'?'#fff2f0':'#f0fff4'}};color:${{kind==='error'?'#a61b1b':'#216e39'}};border:1px solid ${{kind==='error'?'#efb1aa':'#9bd4aa'}};box-shadow:0 10px 30px rgba(31,41,55,.18);font:600 14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif`;
              doc.body.appendChild(toast); window.setTimeout(()=>toast.remove(),8000);
            }}
            async function refresh() {{
              const response=await fetch(`${{api}}/subscription/status`,{{cache:"no-store"}}); const state=await response.json();
              const coordinator=state.coordinator||{{}}; const loginState=state.cookie_login||{{}};
              autoEnabled=Boolean(coordinator.auto_enabled);
              nextAutoAt=coordinator.next_auto_at||"";
              auto.checked=autoEnabled;
              const importScope=coordinator.subscription_import_scope||"pending";
              pendingScope.checked=importScope!=="full";
              fullScope.checked=importScope==="full";
              cookie.textContent=`Cookie：${{state.cookie_saved?"已获取":"未获取"}}`;
              const windowOpen=Boolean(state.cookie_window_open);
              login.disabled=false;
              login.textContent=windowOpen?"获取 Cookie":"登录腾讯文档";
              hint.textContent=windowOpen?"完成登录后，再次点击获取 Cookie":"";
              updateCountdown();
              setParentButtonsDisabled(Boolean(coordinator.active_kind));
              if(cookieActionStarted&&loginState.status==="completed"&&loginState.finished_at&&lastCookieSuccess!==loginState.finished_at){{
                lastCookieSuccess=loginState.finished_at;
                cookieActionStarted=false;
                showToast(loginState.message||"腾讯文档登录凭证已保存");
              }}
              if(state.cookie_refresh_required&&lastCookieError!==state.job_id){{
                lastCookieError=state.job_id;
                try {{const key="customer-rag-cookie-error-job";if(window.localStorage.getItem(key)!==state.job_id){{window.localStorage.setItem(key,state.job_id);showToast("订阅下载失败，请重新获取 Cookie","error")}}}} catch(error){{showToast("订阅下载失败，请重新获取 Cookie","error")}}
              }}
            }}
            auto.addEventListener("change",async()=>{{await fetch(`${{api}}/auto/set?enabled=${{auto.checked?1:0}}&interval=20`,{{cache:"no-store"}});await refresh()}});
            async function setImportScope(scope) {{
              await fetch(`${{api}}/subscription/import-scope?scope=${{scope}}`,{{cache:"no-store"}});
              await refresh();
            }}
            pendingScope.addEventListener("change",()=>setImportScope(pendingScope.checked?"pending":"full"));
            fullScope.addEventListener("change",()=>setImportScope(fullScope.checked?"full":"pending"));
            login.addEventListener("click",async()=>{{
              login.disabled=true;
              cookieActionStarted=true;
              const action=login.textContent.includes("获取")?"read":"open";
              hint.textContent=action==="open"?"正在打开腾讯文档登录页...":"正在读取 Cookie...";
              const response=await fetch(`${{api}}/cookie/login/${{action}}`,{{cache:"no-store"}});
              const result=await response.json();
              if(action==="read"&&result.status!=="completed"){{cookieActionStarted=false;showToast(result.message||"尚未读取到有效 Cookie","error");}}
              await refresh();
            }});
            refresh().catch(()=>{{}}); window.setInterval(()=>refresh().catch(()=>{{}}),1500); window.setInterval(updateCountdown,1000);
          }})();
        </script>
        """,
        height=132,
        scrolling=False,
    )


def render_subscription_task_panel(api_url: str) -> None:
    component_id = "subscription-task"
    components.html(
        f"""
        <style>
          .sub-task {{
            margin: 18px 0 0 0;
            padding: 12px 14px;
            border: 1px solid #e6e9ef;
            border-radius: 8px;
            background: #fff;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            color: #2f3440;
          }}
          .sub-head {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 8px;
          }}
          .sub-title {{
            font-weight: 700;
          }}
          .sub-stop {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            height: 32px;
            padding: 0 14px;
            border: 1px solid #bf3535;
            border-radius: 7px;
            background: #e04b4b;
            color: #fff;
            font-weight: 650;
            cursor: pointer;
          }}
          .sub-stop:disabled {{
            opacity: .55;
            cursor: not-allowed;
          }}
          .sub-line {{
            color: #5f6673;
            font-size: 14px;
            margin: 6px 0;
          }}
          .sub-progress {{
            height: 8px;
            border-radius: 999px;
            background: #edf1f7;
            overflow: hidden;
            margin-top: 8px;
          }}
          .sub-progress > div {{
            width: 0%;
            height: 100%;
            background: #2f7de1;
            transition: width .22s ease;
          }}
          .sub-log {{
            margin-top: 10px;
            max-height: 180px;
            overflow: auto;
            padding: 10px 12px;
            border: 1px solid #edf0f4;
            border-radius: 7px;
            background: #f8fafc;
            color: #4e5665;
            font-size: 12px;
            line-height: 1.55;
          }}
          .sub-log-line {{
            white-space: pre-wrap;
            overflow-wrap: anywhere;
          }}
          .sub-log-line + .sub-log-line {{
            margin-top: 6px;
            padding-top: 6px;
            border-top: 1px dashed #e2e7ee;
          }}
          .subscription-toast {{
            position: fixed;
            right: 24px;
            bottom: 24px;
            z-index: 999999;
            max-width: 460px;
            padding: 14px 18px;
            border: 1px solid #9bd4aa;
            border-radius: 10px;
            background: #f0fff4;
            color: #216e39;
            box-shadow: 0 10px 30px rgba(31, 41, 55, .18);
            font: 600 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            opacity: 0;
            transform: translateY(12px);
            transition: opacity .2s ease, transform .2s ease;
          }}
          .subscription-toast.visible {{ opacity: 1; transform: translateY(0); }}
        </style>
        <div id="{component_id}" class="sub-task" style="display:none">
          <div class="sub-head">
            <div class="sub-title">后台任务</div>
            <button class="sub-stop" data-role="stop">停止当前任务</button>
          </div>
          <div class="sub-line" data-role="status">读取中...</div>
          <div class="sub-line" data-role="detail"></div>
          <div class="sub-progress"><div data-role="bar"></div></div>
          <div class="sub-line" data-role="import-status" style="display:none"></div>
          <div class="sub-progress" data-role="import-progress" style="display:none"><div data-role="import-bar"></div></div>
          <div class="sub-log" data-role="logs"></div>
        </div>
        <script>
          (() => {{
            const api = "{api_url}";
            const root = document.getElementById("{component_id}");
            const stopButton = root.querySelector('[data-role="stop"]');
            const statusLine = root.querySelector('[data-role="status"]');
            const detailLine = root.querySelector('[data-role="detail"]');
            const bar = root.querySelector('[data-role="bar"]');
            const importStatus = root.querySelector('[data-role="import-status"]');
            const importProgress = root.querySelector('[data-role="import-progress"]');
            const importBar = root.querySelector('[data-role="import-bar"]');
            const logsBox = root.querySelector('[data-role="logs"]');
            let lastLogsText = "";
            let userPinnedToBottom = true;
            const runningStatuses = new Set(["running", "waiting_cookie", "rebuilding", "stopping"]);
            let toastTimer = null;
            const labels = {{
              running: "运行中",
              waiting_cookie: "等待登录",
              rebuilding: "处理中",
              stopping: "停止中",
              stopped: "已停止",
              completed: "已完成",
              error: "异常",
              idle: "空闲"
            }};

            function render(state) {{
              if (!state.status || state.status === "idle") {{
                root.style.display = "none";
                return;
              }}
              root.style.display = "block";
              const total = Number(state.total || 0);
              const current = Math.max(0, Number(state.downloaded || 0) + Number(state.skipped || 0) + Number(state.failed || 0));
              const percent = state.percent !== null && state.percent !== undefined && Number.isFinite(Number(state.percent))
                ? Math.max(0, Math.min(100, Number(state.percent)))
                : (total ? Math.min(100, Math.round(current / total * 100)) : 0);
              bar.style.width = `${{percent}}%`;
              const updatedNames = Array.isArray(state.updated_names) ? state.updated_names : [];
              const message = state.status === "completed" && updatedNames.length
                ? `订阅更新完成：${{updatedNames.length}} 个`
                : state.message === "订阅更新完成"
                  ? "下载完成。请在下方点击重新解析文件完成后再重构索引。"
                  : String(state.message || "").includes("解析完成后提交")
                    ? "当前没有待解析订阅文件。订阅修改时间只会在重建向量索引完成后提交。"
                    : (state.message || state.current_name || "");
              statusLine.textContent = `状态：${{labels[state.status] || state.status}} · ${{message}}`;
              detailLine.textContent = `进度：${{current}} / ${{total}} · 下载 ${{state.downloaded || 0}} 个，跳过 ${{state.skipped || 0}} 个，失败 ${{state.failed || 0}} 个`;
              const logs = Array.isArray(state.logs) ? state.logs : [];
              const nextLogsText = logs.length ? logs.join("\\n") : "暂无详细日志";
              const nearBottom = logsBox.scrollHeight - logsBox.scrollTop - logsBox.clientHeight < 24;
              if (nextLogsText !== lastLogsText) {{
                const previousScrollTop = logsBox.scrollTop;
                logsBox.replaceChildren(...(logs.length ? logs : ["暂无详细日志"]).map((log) => {{
                  const line = document.createElement("div");
                  line.className = "sub-log-line";
                  line.textContent = log;
                  return line;
                }}));
                if (userPinnedToBottom && nearBottom) {{
                  logsBox.scrollTop = logsBox.scrollHeight;
                }} else {{
                  logsBox.scrollTop = previousScrollTop;
                }}
                lastLogsText = nextLogsText;
              }}
              stopButton.style.display = runningStatuses.has(state.status) ? "inline-flex" : "none";
              stopButton.disabled = state.status === "stopping";
              if (state.status === "completed") {{
                showCompletionToast(state);
              }}
              if (state.cookie_refresh_required) {{
                showErrorToast("订阅下载失败，请重新获取 Cookie。腾讯文档登录页已打开。", state.job_id);
              }}
            }}
            function formatDuration(seconds) {{
              const total = Math.max(0, Math.round(Number(seconds || 0)));
              const hours = Math.floor(total / 3600);
              const minutes = Math.floor(total / 60);
              const remain = total % 60;
              if (hours) return `${{hours}}小时${{minutes % 60}}分${{remain}}秒`;
              return minutes ? `${{minutes}}分${{remain}}秒` : `${{remain}}秒`;
            }}
            function showCompletionToast(state) {{
              const storageKey = "customer-rag-subscription-toast-job";
              let doc = document;
              try {{ if (window.parent && window.parent.document) doc = window.parent.document; }} catch (error) {{}}
              try {{
                const storage = doc.defaultView.localStorage;
                if (storage.getItem(storageKey) === state.job_id) return;
                storage.setItem(storageKey, state.job_id);
              }} catch (error) {{}}
              const oldToast = doc.getElementById("customer-rag-subscription-toast");
              if (oldToast) oldToast.remove();
              const toast = doc.createElement("div");
              toast.id = "customer-rag-subscription-toast";
              toast.className = "subscription-toast";
              const updatedNames = Array.isArray(state.updated_names) ? state.updated_names : [];
              const resultText = updatedNames.length
                ? `${{updatedNames.length}} 个订阅已更新完毕`
                : "订阅检查完成，无需更新";
              toast.textContent = `${{resultText}}，本次任务耗时 ${{formatDuration(state.duration_seconds)}}`;
              const style = doc.createElement("style");
              style.textContent = `.subscription-toast{{position:fixed;right:24px;bottom:24px;z-index:999999;max-width:460px;padding:14px 18px;border:1px solid #9bd4aa;border-radius:10px;background:#f0fff4;color:#216e39;box-shadow:0 10px 30px rgba(31,41,55,.18);font:600 14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;opacity:0;transform:translateY(12px);transition:opacity .2s ease,transform .2s ease}}.subscription-toast.visible{{opacity:1;transform:translateY(0)}}`;
              doc.head.appendChild(style);
              doc.body.appendChild(toast);
              requestAnimationFrame(() => toast.classList.add("visible"));
              if (toastTimer) window.clearTimeout(toastTimer);
              toastTimer = window.setTimeout(() => {{
                toast.classList.remove("visible");
                window.setTimeout(() => {{ toast.remove(); style.remove(); }}, 250);
              }}, 8000);
            }}
            function showErrorToast(text, jobId) {{
              const storageKey = "customer-rag-cookie-error-job";
              try {{ if (window.localStorage.getItem(storageKey) === jobId) return; window.localStorage.setItem(storageKey, jobId); }} catch (error) {{}}
              let doc = document; try {{ if (window.parent && window.parent.document) doc = window.parent.document; }} catch (error) {{}}
              const toast = doc.createElement("div"); toast.textContent = text;
              toast.style.cssText = 'position:fixed;right:24px;bottom:24px;z-index:999999;max-width:460px;padding:14px 18px;border:1px solid #efb1aa;border-radius:10px;background:#fff2f0;color:#a61b1b;box-shadow:0 10px 30px rgba(31,41,55,.18);font:600 14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
              doc.body.appendChild(toast); window.setTimeout(() => toast.remove(), 8000);
            }}
            logsBox.addEventListener("scroll", () => {{
              userPinnedToBottom = logsBox.scrollHeight - logsBox.scrollTop - logsBox.clientHeight < 24;
            }});
            async function fetchStatus() {{
              const response = await fetch(`${{api}}/subscription/status`, {{ cache: "no-store" }});
              render(await response.json());
              const rawResponse = await fetch(`${{api}}/raw/status`, {{ cache: "no-store" }});
              const raw = await rawResponse.json();
              if (raw.status && raw.status !== "idle") {{
                const rawPercent = Math.max(0, Math.min(100, Number(raw.percent || 0)));
                importStatus.style.display = "block";
                importProgress.style.display = "block";
                importStatus.textContent = `导入数据：${{raw.message || raw.error || raw.status}} · ${{rawPercent}}%`;
                importBar.style.width = `${{rawPercent}}%`;
              }} else {{
                importStatus.style.display = "none";
                importProgress.style.display = "none";
              }}
            }}
            stopButton.addEventListener("click", async () => {{
              stopButton.disabled = true;
              await fetch(`${{api}}/subscription/stop`, {{ cache: "no-store" }});
              await fetchStatus();
            }});
            fetchStatus().catch(() => {{}});
            window.setInterval(() => fetchStatus().catch(() => {{}}), 1800);
          }})();
        </script>
        """,
        height=310,
        scrolling=False,
    )


def render_qa_query_panel(api_url: str, tags: list[str], total_items: int, top_k: int) -> None:
    components.html(
        f"""
        <style>
          .qa-panel {{display:grid;grid-template-columns:minmax(220px,.28fr) minmax(420px,.72fr);gap:28px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#2f3440}}
          .qa-card {{border:1px solid #e6e9ef;border-radius:8px;background:#fff;padding:14px}}
          .qa-title {{font-size:18px;font-weight:750;margin-bottom:12px}}
          .qa-muted {{color:#697181;font-size:13px;margin:8px 0}}
          .qa-tags {{display:grid;gap:7px;max-height:280px;overflow:auto}}
          .qa-tags label {{display:flex;align-items:center;gap:8px;font-size:14px}}
          .qa-tags input {{width:15px;height:15px;accent-color:#2f7de1}}
          .qa-input-row {{display:grid;grid-template-columns:1fr 112px;gap:10px}}
          .qa-input {{height:42px;border:1px solid #d6dbe5;border-radius:7px;padding:0 12px;font-size:15px;outline:none}}
          .qa-input:focus {{border-color:#2f7de1;box-shadow:0 0 0 3px rgba(47,125,225,.12)}}
          .qa-button {{height:42px;border:1px solid #1d64bb;border-radius:7px;background:#2f7de1;color:#fff;font-weight:700;cursor:pointer}}
          .qa-button:disabled {{cursor:not-allowed;opacity:.62}}
          .qa-result {{margin-top:16px}}
          .qa-section-title {{font-weight:750;margin:16px 0 8px}}
          .qa-answer,.qa-source-text {{white-space:pre-wrap;line-height:1.65}}
          .qa-warning {{display:none;margin-top:12px;color:#8a5a00;background:#fff7e0;border:1px solid #f2d58a;border-radius:7px;padding:9px 10px}}
          .qa-source {{border:1px solid #edf0f4;border-radius:7px;padding:10px 12px;margin-top:8px;background:#fbfcfe}}
          .qa-source summary {{cursor:pointer;font-weight:650}}
          @media (max-width:900px) {{.qa-panel{{grid-template-columns:1fr}}.qa-input-row{{grid-template-columns:1fr}}}}
        </style>
        <div id="qa-query-panel" class="qa-panel">
          <div class="qa-card">
            <div class="qa-title">检索范围</div>
            <div class="qa-muted">当前最多引用 {top_k} 条资料。</div>
            <div class="qa-muted">语料总数 {total_items}，Tag 数量 {len(tags)}</div>
            <div class="qa-tags" data-role="tags"></div>
          </div>
          <div class="qa-card">
            <div class="qa-title">知识库问答</div>
            <div class="qa-input-row">
              <input class="qa-input" data-role="question" placeholder="例如：有什么电饭煲？">
              <button class="qa-button" data-role="ask">查询</button>
            </div>
            <div class="qa-warning" data-role="warning"></div>
            <div class="qa-result" data-role="result"></div>
          </div>
        </div>
        <script>
          (() => {{
            const api = "{api_url}";
            const tags = {json.dumps(tags, ensure_ascii=False)};
            const root = document.getElementById("qa-query-panel");
            const tagsBox = root.querySelector('[data-role="tags"]');
            const question = root.querySelector('[data-role="question"]');
            const button = root.querySelector('[data-role="ask"]');
            const warning = root.querySelector('[data-role="warning"]');
            const result = root.querySelector('[data-role="result"]');
            const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
            tagsBox.innerHTML = tags.length ? tags.map((tag, index) => `<label><input type="checkbox" value="${{esc(tag)}}" id="qa-tag-${{index}}"><span>${{esc(tag)}}</span></label>`).join("") : '<div class="qa-muted">暂无可筛选 Tag</div>';
            const selectedTags = () => Array.from(tagsBox.querySelectorAll('input:checked')).map((item) => item.value);
            const sourceHtml = (source, index) => `<details class="qa-source"><summary>${{index + 1}}. ${{esc(source.title || "未命名")}} · 相似度 ${{Number(source.score || 0).toFixed(3)}}</summary><div class="qa-muted">${{esc(source.location || "")}}${{Array.isArray(source.tags)&&source.tags.length ? " · Tag：" + esc(source.tags.join("、")) : ""}}</div><div class="qa-source-text">${{esc(source.text || "")}}</div></details>`;
            async function ask() {{
              const text = question.value.trim();
              if (!text) {{ result.innerHTML = '<div class="qa-muted">请输入问题。</div>'; return; }}
              button.disabled = true; button.textContent = "查询中"; warning.style.display = "none";
              result.innerHTML = '<div class="qa-muted">正在检索并生成答案，请稍候。</div>';
              const params = new URLSearchParams({{question: text}});
              selectedTags().forEach((tag) => params.append("tag", tag));
              try {{
                const response = await fetch(`${{api}}/qa/ask?${{params.toString()}}`, {{cache:"no-store"}});
                const payload = await response.json();
                if (!response.ok) throw new Error(payload.error || response.statusText);
                if (payload.warning) {{ warning.textContent = payload.warning; warning.style.display = "block"; }}
                const sources = Array.isArray(payload.sources) ? payload.sources : [];
                result.innerHTML = `<div class="qa-section-title">答案</div><div class="qa-answer">${{esc(payload.answer || "")}}</div><div class="qa-section-title">引用资料</div>${{sources.length ? sources.map(sourceHtml).join("") : '<div class="qa-muted">暂无引用资料</div>'}}`;
              }} catch (error) {{
                result.innerHTML = `<div class="qa-warning" style="display:block">查询失败：${{esc(error.message || error)}}</div>`;
              }} finally {{
                button.disabled = false; button.textContent = "查询";
              }}
            }}
            button.addEventListener("click", ask);
            question.addEventListener("keydown", (event) => {{ if (event.key === "Enter") ask(); }});
          }})();
        </script>
        """,
        height=720,
        scrolling=True,
    )


def render_corpus_query_panel(api_url: str, sources: list[str], tags: list[str]) -> None:
    components.html(
        f"""
        <style>
          .corpus-query {{border:1px solid #e6e9ef;border-radius:8px;background:#fff;padding:14px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#2f3440}}
          .cq-title {{font-size:18px;font-weight:750;margin-bottom:12px}}
          .cq-controls {{display:grid;grid-template-columns:minmax(220px,1.4fr) minmax(160px,.9fr) minmax(160px,.9fr) 110px 150px 92px;gap:10px;align-items:end}}
          .cq-field label {{display:block;color:#697181;font-size:12px;margin-bottom:5px}}
          .cq-field input,.cq-field select {{width:100%;height:38px;box-sizing:border-box;border:1px solid #d6dbe5;border-radius:7px;padding:0 10px;background:#fff;color:#2f3440}}
          .cq-field select[multiple] {{height:86px;padding:6px 8px}}
          .cq-button {{height:38px;border:1px solid #1d64bb;border-radius:7px;background:#2f7de1;color:#fff;font-weight:700;cursor:pointer}}
          .cq-summary {{margin:12px 0 8px;color:#697181;font-size:13px}}
          .cq-table-wrap {{max-height:470px;overflow:auto;border:1px solid #edf0f4;border-radius:8px}}
          .cq-table {{width:100%;min-width:920px;border-collapse:collapse;font-size:14px}}
          .cq-table th {{position:sticky;top:0;background:#f7f8fb;color:#7c8493;text-align:left;padding:9px 10px;border-bottom:1px solid #e6e9ef}}
          .cq-table td {{padding:9px 10px;border-bottom:1px solid #edf0f4;vertical-align:top}}
          .cq-summary-cell {{max-width:420px;color:#4e5665;line-height:1.45}}
          @media (max-width:1100px) {{.cq-controls{{grid-template-columns:1fr 1fr}}}}
        </style>
        <div id="corpus-query-panel" class="corpus-query">
          <div class="cq-title">语料库快速查询</div>
          <div class="cq-controls">
            <div class="cq-field"><label>搜索</label><input data-role="keyword" placeholder="搜索标题、来源、正文"></div>
            <div class="cq-field"><label>来源</label><select data-role="sources" multiple></select></div>
            <div class="cq-field"><label>Tag</label><select data-role="tags" multiple></select></div>
            <div class="cq-field"><label>图片</label><select data-role="image"><option>全部</option><option>有图片</option><option>无图片</option></select></div>
            <div class="cq-field"><label>排序</label><select data-role="sort"><option>更新时间倒序</option><option>创建时间倒序</option><option>标题 A-Z</option></select></div>
            <button class="cq-button" data-role="search">查询</button>
          </div>
          <div class="cq-summary" data-role="summary">点击查询查看语料。</div>
          <div class="cq-table-wrap" data-role="table"></div>
        </div>
        <script>
          (() => {{
            const api = "{api_url}";
            const sourceOptions = {json.dumps(sources, ensure_ascii=False)};
            const tagOptions = {json.dumps(tags, ensure_ascii=False)};
            const root = document.getElementById("corpus-query-panel");
            const keyword = root.querySelector('[data-role="keyword"]');
            const sourceSelect = root.querySelector('[data-role="sources"]');
            const tagSelect = root.querySelector('[data-role="tags"]');
            const image = root.querySelector('[data-role="image"]');
            const sort = root.querySelector('[data-role="sort"]');
            const button = root.querySelector('[data-role="search"]');
            const summary = root.querySelector('[data-role="summary"]');
            const table = root.querySelector('[data-role="table"]');
            const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
            const fill = (select, values) => select.innerHTML = values.map((value) => `<option value="${{esc(value)}}">${{esc(value)}}</option>`).join("");
            const selected = (select) => Array.from(select.selectedOptions).map((option) => option.value);
            fill(sourceSelect, sourceOptions); fill(tagSelect, tagOptions);
            async function search() {{
              button.disabled = true; button.textContent = "查询中"; summary.textContent = "正在查询语料。";
              const params = new URLSearchParams({{keyword: keyword.value.trim(), image: image.value, sort: sort.value, limit: "50"}});
              selected(sourceSelect).forEach((value) => params.append("source", value));
              selected(tagSelect).forEach((value) => params.append("tag", value));
              try {{
                const response = await fetch(`${{api}}/corpus/search?${{params.toString()}}`, {{cache:"no-store"}});
                const payload = await response.json();
                if (!response.ok) throw new Error(payload.error || response.statusText);
                const rows = Array.isArray(payload.rows) ? payload.rows : [];
                summary.textContent = `共 ${{payload.total || 0}} 条语料，匹配 ${{payload.matched || 0}} 条，当前显示 ${{rows.length}} 条。`;
                table.innerHTML = `<table class="cq-table"><thead><tr><th>标题</th><th>来源</th><th>Tag</th><th>摘要</th><th>图片</th><th>更新时间</th></tr></thead><tbody>${{rows.map((row) => `<tr><td>${{esc(row.title)}}</td><td>${{esc(row.source)}}</td><td>${{esc(Array.isArray(row.tags) ? row.tags.join("、") : "")}}</td><td class="cq-summary-cell">${{esc(row.summary)}}</td><td>${{Number(row.image_count || 0)}}</td><td>${{esc(row.updated_at)}}</td></tr>`).join("")}}</tbody></table>`;
              }} catch (error) {{
                summary.textContent = `查询失败：${{String(error.message || error)}}`;
              }} finally {{
                button.disabled = false; button.textContent = "查询";
              }}
            }}
            button.addEventListener("click", search);
            keyword.addEventListener("keydown", (event) => {{ if (event.key === "Enter") search(); }});
          }})();
        </script>
        """,
        height=650,
        scrolling=True,
    )


def existing_images(paths: list[str]) -> list[str]:
    return [path for path in paths if Path(path).exists()]


def render_images(paths: list[str], limit: int = 3) -> None:
    images = existing_images(paths)[:limit]
    for image in images:
        width = 720 if "/_generated/order_flow/" in image.replace("\\", "/") else 180
        st.image(image, width=width)


def order_flow_images(paths: list[str]) -> list[str]:
    return [
        path
        for path in existing_images(paths)
        if "/_generated/order_flow/" in path.replace("\\", "/")
    ]


def render_answer_with_inline_images(answer: str) -> None:
    buffer: list[str] = []
    image_pattern = re.compile(r"^\s*(?:[-•]\s*)?图片\s*[：:]\s*(.+?)\s*$")
    for line in str(answer or "").splitlines():
        match = image_pattern.match(line)
        if not match:
            buffer.append(line)
            continue

        if buffer:
            st.markdown("\n".join(buffer))
            buffer = []

        image_path = resolve_answer_image_path(match.group(1))
        if image_path:
            st.image(str(image_path), width=760)
        else:
            st.caption("图片文件不存在，可能需要重新解析原始文件。")

    if buffer:
        st.markdown("\n".join(buffer))


def resolve_answer_image_path(raw_path: str) -> Path | None:
    cleaned = str(raw_path or "").strip().strip('"').strip("'")
    cleaned = cleaned.replace("\\\\", "\\")
    if not cleaned:
        return None
    variants = [cleaned]
    if "tencent_docs_generated" in cleaned:
        variants.append(cleaned.replace("tencent_docs_generated", "tencent_docs\\_generated"))
    candidates: list[Path] = []
    for variant in variants:
        path = Path(variant)
        candidates.append(path)
        if not path.is_absolute():
            candidates.append(Path.cwd() / path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def source_label(source: str) -> str:
    if source == "manual":
        return "手动录入"
    return Path(source).name


def parse_tags(text: str) -> list[str]:
    tags: list[str] = []
    normalized = (
        text.replace("，", "\n")
        .replace(",", "\n")
        .replace("；", "\n")
        .replace(";", "\n")
        .replace("|", "\n")
        .replace("、", "\n")
    )
    for part in normalized.splitlines():
        value = part.strip()
        if value and value not in tags:
            tags.append(value)
    return tags


def tag_text(tags: list[str]) -> str:
    return ", ".join(tags)


def tag_badges(tags: list[str]) -> str:
    return "、".join(tags) if tags else "未分类"


def category_alias_rows() -> list[dict[str, str]]:
    brands = category_brands()
    return [
        {
            "标准类目": category,
            "同义词": "、".join(aliases),
            "在售品牌数": str(len(brands.get(category, []))),
            "品牌示例": preview_terms(brands.get(category, [])),
        }
        for category, aliases in category_aliases().items()
    ]


def category_alias_rows_to_config(
    rows: list[dict],
    current_brands: dict[str, list[str]],
) -> tuple[dict[str, list[str]], dict[str, list[str]], list[str]]:
    aliases: dict[str, list[str]] = {}
    brands: dict[str, list[str]] = {}
    errors: list[str] = []
    for index, row in enumerate(rows, start=1):
        category = str(row.get("标准类目", "") or "").strip()
        if not category:
            continue
        if category in aliases:
            errors.append(f"第 {index} 行类目重复：{category}")
            continue
        alias_text = str(row.get("同义词", "") or "")
        alias_values = [alias for alias in parse_tags(alias_text) if alias != category]
        aliases[category] = alias_values
        brands[category] = list(current_brands.get(category, []))
    if not aliases:
        errors.append("至少需要保留一个商品类目。")
    return aliases, brands, errors


def preview_terms(values: list[str], limit: int = 4) -> str:
    terms = [str(value).strip() for value in values if str(value).strip()]
    if not terms:
        return ""
    preview = "、".join(terms[:limit])
    if len(terms) > limit:
        preview += f" 等 {len(terms)} 个"
    return preview


def format_bytes(size: int | None) -> str:
    if size is None:
        return "未知大小"
    value = float(size)
    for unit in ["B", "KB", "MB", "GB"]:
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def uploaded_file_tag_key(index: int, name: str, size: int) -> str:
    return f"upload_file_tags_{index}_{name}_{size}"


def toggle_corpus_page_selection(item_ids: list[str]) -> None:
    next_selected = not st.session_state.get("corpus_select_all_active", False)
    for item_id in item_ids:
        st.session_state[f"corpus_selected_{item_id}"] = next_selected
    st.session_state["corpus_select_all_active"] = next_selected
    st.session_state["corpus_table_version"] = st.session_state.get("corpus_table_version", 0) + 1
    st.session_state["confirm_delete_selected"] = False


def is_user_source_file(path: Path) -> bool:
    if path.name == ".gitkeep":
        return False
    if path.suffix.lower() not in {".csv", ".docx", ".md", ".pdf", ".txt", ".xls", ".xlsx"}:
        return False
    try:
        relative = path.relative_to(cfg.raw_data_dir)
    except ValueError:
        return False
    return "_assets" not in relative.parts and "_generated" not in relative.parts


def prompt_settings_path() -> Path:
    return cfg.index_dir / "prompt_settings.json"


def subscriptions_path() -> Path:
    return cfg.index_dir / "tencent_doc_subscriptions.json"


def load_system_prompt() -> str:
    path = prompt_settings_path()
    if not path.exists():
        return DEFAULT_SYSTEM_PROMPT
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return DEFAULT_SYSTEM_PROMPT
    prompt = str(data.get("system_prompt", "")).strip()
    return prompt or DEFAULT_SYSTEM_PROMPT


def save_system_prompt(prompt: str) -> None:
    path = prompt_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"system_prompt": prompt.strip()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    st.session_state["system_prompt_override"] = prompt.strip() or DEFAULT_SYSTEM_PROMPT


def current_system_prompt() -> str:
    value = st.session_state.get("system_prompt_override")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return load_system_prompt()


def subscription_rows_to_items(rows: list[dict]) -> list[TencentDocSubscription]:
    subscriptions: list[TencentDocSubscription] = []
    for row in rows:
        name = str(row.get("名称", "")).strip()
        url = str(row.get("腾讯文档地址", "")).strip()
        if not name or not url:
            continue
        subscriptions.append(
            TencentDocSubscription(
                name=name,
                url=url,
                tags=parse_tags(str(row.get("Tag", ""))),
                enabled=bool(row.get("启用", True)),
                last_status=str(row.get("状态", "") or ""),
                last_modified=display_datetime(row.get("最后修改", "")),
            )
        )
    return subscriptions


def parse_batch_subscription_text(text: str, default_tags: str = "") -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    index = 0
    while index < len(lines):
        line = lines[index]
        name = ""
        url = ""
        bracket_match = re.fullmatch(r"[【\[](.+?)[】\]]", line)
        if bracket_match:
            name = bracket_match.group(1).strip()
            index += 1
            if index < len(lines):
                url = lines[index].strip()
        elif line.startswith("http"):
            errors.append(f"第 {index + 1} 行缺少订阅名称：{line}")
            index += 1
            continue
        else:
            name = line.strip("【】[] ")
            index += 1
            if index < len(lines):
                url = lines[index].strip()

        if not name or not url:
            errors.append(f"订阅信息不完整：{line}")
            index += 1
            continue
        if not re.match(r"^https://docs\.qq\.com/(?:sheet|doc|slide)/", url):
            errors.append(f"{name} 的腾讯文档链接格式不正确：{url}")
            index += 1
            continue
        rows.append(
            {
                "": True,
                "名称": name,
                "最后修改": "",
                "状态": "待同步",
                "腾讯文档地址": url,
                "Tag": default_tags,
            }
        )
        index += 1
    return rows, errors


@st.dialog("批量增加订阅", width="large")
def batch_add_subscription_dialog() -> None:
    st.caption("按“名称 + 链接”的顺序粘贴；每个订阅占两行，第一行名称，第二行腾讯文档链接。")
    pasted_text = st.text_area(
        "粘贴订阅清单",
        height=360,
        placeholder="6月电视清单-26年618\nhttps://docs.qq.com/sheet/DVnZMQ055cnFHQmRL\n海尔清单-26年618\nhttps://docs.qq.com/sheet/DVkhVTWZmZ2t0U1Zo",
    )
    st.markdown(
        """
        <style>
          div[data-testid="stElementContainer"]:has(.batch-subscription-footer)
          + div[data-testid="stLayoutWrapper"] div[data-testid="stHorizontalBlock"] {
              display: flex !important;
              align-items: flex-end !important;
              gap: 1rem;
          }
          div[data-testid="stElementContainer"]:has(.batch-subscription-footer)
          + div[data-testid="stLayoutWrapper"] div[data-testid="stColumn"] {
              display: flex !important;
              flex-direction: column !important;
              justify-content: flex-end !important;
          }
          div[data-testid="stElementContainer"]:has(.batch-subscription-footer)
          + div[data-testid="stLayoutWrapper"] button {
              display: flex !important;
              align-items: center !important;
              justify-content: center !important;
              height: 48px !important;
              margin: 0 !important;
          }
        </style>
        <div class="batch-subscription-footer"></div>
        """,
        unsafe_allow_html=True,
    )
    tag_col, confirm_col = st.columns([0.68, 0.32], gap="small")
    default_tags = tag_col.text_input("总体 Tag", value="", placeholder="例如：家电, 618")
    confirm = confirm_col.button("确认增加", type="primary", use_container_width=True)
    if confirm:
        rows, errors = parse_batch_subscription_text(pasted_text, default_tags=default_tags)
        if errors:
            for error in errors:
                st.warning(error)
        if not rows:
            st.warning("没有识别到可增加的订阅。")
            return
        existing_rows = list(st.session_state.get("import_subscription_rows", []))
        existing_urls = {str(row.get("腾讯文档地址", "")).strip() for row in existing_rows}
        new_rows = [row for row in rows if row["腾讯文档地址"] not in existing_urls]
        skipped = len(rows) - len(new_rows)
        st.session_state["import_subscription_rows"] = new_rows + existing_rows
        st.session_state["import_subscription_editor_version"] = (
            int(st.session_state.get("import_subscription_editor_version", 0)) + 1
        )
        queue_ui_notice("success", f"已增加 {len(new_rows)} 个订阅" + (f"，跳过重复 {skipped} 个" if skipped else ""))


@st.dialog("本机性能设置", width="large")
def machine_settings_dialog() -> None:
    current = load_config()
    batch_options = [16, 32, 64, 128, 256]
    thread_options = [1, 2, 4, 6, 8, 12, 16, 24, 32]
    gpu_layer_options = [0, 8, 16, 24, 32, 48, 64, 99]
    ctx_options = [2048, 4096, 8192, 16384]
    llm_batch_options = [64, 128, 256, 512, 1024]

    with st.form("machine_settings_form"):
        st.subheader("常用性能")
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            embedding_batch_size = st.selectbox(
                "Embedding batch size",
                batch_options,
                index=batch_options.index(current.embedding_batch_size)
                if current.embedding_batch_size in batch_options
                else batch_options.index(32),
            )
        with col_b:
            n_threads = st.selectbox(
                "LLM CPU 线程数",
                thread_options,
                index=thread_options.index(current.llm.n_threads)
                if current.llm.n_threads in thread_options
                else thread_options.index(4),
            )
        with col_c:
            n_gpu_layers = st.selectbox(
                "LLM GPU 层数",
                gpu_layer_options,
                index=gpu_layer_options.index(current.llm.n_gpu_layers)
                if current.llm.n_gpu_layers in gpu_layer_options
                else 0,
            )

        st.subheader("高级性能")
        col_d, col_e = st.columns(2)
        with col_d:
            n_ctx = st.selectbox(
                "LLM 上下文长度",
                ctx_options,
                index=ctx_options.index(current.llm.n_ctx)
                if current.llm.n_ctx in ctx_options
                else ctx_options.index(4096),
            )
        with col_e:
            num_batch = st.selectbox(
                "LLM num_batch",
                llm_batch_options,
                index=llm_batch_options.index(current.llm.num_batch)
                if current.llm.num_batch in llm_batch_options
                else llm_batch_options.index(128),
            )

        with st.expander("模型路径"):
            embedding_model_path = st.text_input("Embedding 模型路径", value=str(current.embedding_model_path))
            llm_model_path = st.text_input("LLM 模型路径", value=str(current.llm_model_path))

        submitted = st.form_submit_button("保存设置", type="primary")
        if submitted:
            save_machine_config(
                {
                    "embedding_model_path": embedding_model_path.strip() or str(current.embedding_model_path),
                    "llm_model_path": llm_model_path.strip() or str(current.llm_model_path),
                    "embedding_batch_size": int(embedding_batch_size),
                },
                {
                    "n_threads": int(n_threads),
                    "n_gpu_layers": int(n_gpu_layers),
                    "n_ctx": int(n_ctx),
                    "num_batch": int(num_batch),
                },
            )
            queue_ui_notice("success", "本机性能设置已保存。Embedding 配置将在下次重建索引时生效，LLM 配置需重启服务后生效。")
            st.rerun()


cfg = load_config()
pipeline = get_pipeline()
system_prompt = load_system_prompt()

items = cached_corpus_items(corpus_signature())
tag_options = sorted({tag for item in items for tag in item.tags})


def submit_qa_query() -> None:
    question = str(st.session_state.get("qa_query_question", "")).strip()
    if not question:
        return
    st.session_state["qa_query_result"] = pipeline.ask(
        question,
        system_prompt=current_system_prompt(),
        tags=list(st.session_state.get("qa_query_tags", [])),
    )

title_col, settings_col = st.columns([1, 0.16], vertical_alignment="center")
with title_col:
    st.title("本地腾讯文档 RAG")
    st.caption("离线语料管理、向量检索和本地大模型问答")
with settings_col:
    if st.button("设置", key="machine_settings_open", use_container_width=True):
        machine_settings_dialog()
render_ui_notice()

tab_qa, tab_corpus, tab_import, tab_prompt, tab_categories = st.tabs(
    ["问答", "语料管理", "导入文件", "Prompt 设置", "商品类目"]
)

with tab_qa:
    left, right = st.columns([0.28, 0.72], gap="large")

    with left:
        st.subheader("检索范围")
        qa_tags_input = st.multiselect(
            "按 Tag 限定",
            tag_options,
            key="qa_query_tags",
            placeholder="默认检索全部语料",
        )
        st.caption(f"当前最多引用 {cfg.top_k} 条资料。")
        st.divider()
        st.metric("语料总数", len(items))
        st.metric("Tag 数量", len(tag_options))

    with right:
        st.subheader("知识库问答")
        if not items:
            st.info("当前语料库为空，请先在“导入文件”或“语料管理”中添加语料。")
        st.text_input(
            "问题",
            key="qa_query_question",
            placeholder="例如：有什么电饭煲？",
            on_change=submit_qa_query,
        )

        result = st.session_state.get("qa_query_result")
        if result:
            if result.warning:
                st.warning(result.warning)

            st.markdown("#### 答案")
            render_answer_with_inline_images(result.answer)

            st.markdown("#### 引用资料")
            for i, source in enumerate(result.sources, start=1):
                with st.expander(f"{i}. {source.title}  相似度 {source.score:.3f}"):
                    st.caption(source.location)
                    st.write(source.text)
                    if source.tags:
                        st.caption(f"Tag：{tag_badges(source.tags)}")
                    if source.image_paths:
                        render_images(source.image_paths)

with tab_corpus:
    st.subheader("语料管理")

    items = cached_corpus_items(corpus_signature())
    tag_options = sorted({tag for item in items for tag in item.tags})
    source_options = sorted({source_label(item.source) for item in items})

    corpus_filter_panel, corpus_list_panel = st.columns([0.24, 0.76], gap="medium")

    with corpus_filter_panel:
        with st.container(border=True):
            st.markdown("#### 筛选")
            keyword = st.text_input("搜索", placeholder="搜索标题、来源、正文")
            selected_sources = st.multiselect("来源", source_options, placeholder="全部来源")
            selected_tags = st.multiselect("Tag", tag_options, placeholder="全部分类")
            image_filter = st.radio("图片", ["全部", "有图片", "无图片"], horizontal=True)
            sort_by = st.selectbox("排序", ["更新时间倒序", "创建时间倒序", "标题 A-Z"])

    selected_tag_set = set(selected_tags)
    filtered_items = [
        item
        for item in items
        if (
            not keyword
            or keyword.lower() in item.title.lower()
            or keyword.lower() in item.location.lower()
            or keyword.lower() in item.text.lower()
            or keyword.lower() in source_label(item.source).lower()
            or any(keyword.lower() in tag.lower() for tag in item.tags)
        )
        and (not selected_sources or source_label(item.source) in selected_sources)
        and (not selected_tag_set or selected_tag_set.issubset(set(item.tags)))
        and (
            image_filter == "全部"
            or (image_filter == "有图片" and bool(existing_images(item.image_paths)))
            or (image_filter == "无图片" and not existing_images(item.image_paths))
        )
    ]
    if sort_by == "更新时间倒序":
        filtered_items.sort(key=lambda item: item.updated_at, reverse=True)
    elif sort_by == "创建时间倒序":
        filtered_items.sort(key=lambda item: item.created_at, reverse=True)
    else:
        filtered_items.sort(key=lambda item: item.title)

    with corpus_filter_panel:
        with st.container(border=True):
            st.markdown("#### 统计")
            stat_a, stat_b = st.columns(2)
            stat_a.metric("总语料", len(items))
            stat_b.metric("匹配结果", len(filtered_items))

    with corpus_list_panel:
        with st.container(border=True):
            st.markdown("#### 语料库")

            st.markdown('<div class="corpus-toolbar-style"></div>', unsafe_allow_html=True)
            toolbar = st.columns([0.9, 0.9, 1.15, 0.8, 0.75, 1.0, 0.45, 0.9], gap="small")
            if toolbar[0].button("新增语料", use_container_width=True):
                st.session_state["show_add_corpus_form"] = not st.session_state.get("show_add_corpus_form", False)
            max_rows = toolbar[4].selectbox("显示数量", [25, 50, 100, 200], index=1, label_visibility="collapsed")

            display_items = filtered_items[: int(max_rows)]
            page_ids = [item.id for item in display_items]
            page_signature = "|".join(page_ids)
            if st.session_state.get("corpus_page_signature") != page_signature:
                st.session_state["corpus_page_signature"] = page_signature
                st.session_state["corpus_select_all_active"] = False

            select_toggle_label = (
                "取消全选" if st.session_state.get("corpus_select_all_active", False) else "全选当前页"
            )
            toolbar[1].button(
                select_toggle_label,
                use_container_width=True,
                on_click=toggle_corpus_page_selection,
                args=(page_ids,),
                disabled=not page_ids,
            )
            batch_tags = toolbar[2].text_input("批量追加 Tag", placeholder="批量追加 Tag", label_visibility="collapsed")

            pending_selected_ids = {
                item_id for item_id in page_ids if st.session_state.get(f"corpus_selected_{item_id}", False)
            }
            if toolbar[3].button("应用 Tag", use_container_width=True):
                tags_to_add = parse_tags(batch_tags)
                if not pending_selected_ids:
                    st.info("请先勾选语料。")
                elif not tags_to_add:
                    st.warning("请先输入要追加的 Tag。")
                else:
                    changed = pipeline.add_tags_to_corpus_many(pending_selected_ids, tags_to_add)
                    queue_ui_notice("success", f"已更新 {changed} 条语料的 Tag。请重建向量索引后用于按分类检索。")
            if toolbar[5].button("删除已选", type="secondary", use_container_width=True):
                if not pending_selected_ids:
                    st.info("请先勾选语料。")
                else:
                    st.session_state["confirm_delete_selected"] = True
            toolbar[7].caption("索引重建请使用“导入文件”页的异步按钮")

            if st.session_state.get("show_add_corpus_form", False):
                with st.form("add_corpus_form", clear_on_submit=True):
                    title = st.text_input("标题", placeholder="例如：客户退款流程")
                    tags_input = st.text_input("Tag", placeholder="例如：小家电, 618, 电饭煲")
                    text = st.text_area("内容", height=150, placeholder="输入要加入知识库的文本...")
                    submitted = st.form_submit_button("保存新增", type="primary")
                if submitted:
                    try:
                        pipeline.add_corpus(title, text, tags=parse_tags(tags_input))
                        queue_ui_notice("success", "已新增语料。请重建向量索引后用于问答。")
                        st.session_state["show_add_corpus_form"] = False
                    except ValueError as exc:
                        st.error(str(exc))

            if not filtered_items:
                st.info("没有匹配的语料。")
            elif len(filtered_items) > len(display_items):
                st.caption(f"共 {len(filtered_items)} 条匹配结果，当前显示前 {len(display_items)} 条。")

            table_rows = [
                {
                    "选择": bool(st.session_state.get(f"corpus_selected_{item.id}", False)),
                    "标题": item.title,
                    "来源": source_label(item.source),
                    "Tag": tag_badges(item.tags),
                    "摘要": item.text[:180].replace("\n", " "),
                    "图片": len(existing_images(item.image_paths)) if item.image_paths else 0,
                    "更新时间": item.updated_at[:16].replace("T", " "),
                    "_id": item.id,
                }
                for item in display_items
            ]
            edited_table = st.data_editor(
                pd.DataFrame(table_rows),
                key=f"corpus_table_{st.session_state.get('corpus_table_version', 0)}",
                hide_index=True,
                use_container_width=True,
                height=min(560, 92 + max(1, len(display_items)) * 42),
                disabled=["标题", "来源", "Tag", "摘要", "图片", "更新时间"],
                column_config={
                    "选择": st.column_config.CheckboxColumn("选择", width="small"),
                    "标题": st.column_config.TextColumn("标题", width="medium"),
                    "来源": st.column_config.TextColumn("来源", width="small"),
                    "Tag": st.column_config.TextColumn("Tag", width="small"),
                    "摘要": st.column_config.TextColumn("摘要", width="large"),
                    "图片": st.column_config.NumberColumn("图片", width="small"),
                    "更新时间": st.column_config.TextColumn("更新时间", width="small"),
                    "_id": None,
                },
            )

            selected_ids: set[str] = set()
            if not edited_table.empty:
                for row in edited_table.to_dict("records"):
                    item_id = str(row["_id"])
                    selected = bool(row["选择"])
                    st.session_state[f"corpus_selected_{item_id}"] = selected
                    if selected:
                        selected_ids.add(item_id)
            st.session_state["corpus_select_all_active"] = bool(page_ids) and all(
                st.session_state.get(f"corpus_selected_{item_id}", False) for item_id in page_ids
            )

            bottom_actions = st.columns([1, 5], gap="small")
            if bottom_actions[0].button("编辑选中", use_container_width=True):
                if len(selected_ids) != 1:
                    st.info("请只勾选一条语料进行编辑。")
                else:
                    st.session_state["editing_corpus_id"] = next(iter(selected_ids))
            bottom_actions[1].caption(
                "删除只会移除语料库记录，不会删除原始上传文件。"
            )

            if st.session_state.get("confirm_delete_selected", False):
                warn_cols = st.columns([2, 1, 1], gap="medium")
                warn_cols[0].warning("确认删除已选语料？")
                if warn_cols[1].button("确认删除", type="secondary", use_container_width=True):
                    removed = pipeline.delete_corpus_many(selected_ids)
                    queue_ui_notice("success", f"已删除 {removed} 条语料。请重建向量索引后用于问答。")
                    for item_id in selected_ids:
                        st.session_state.pop(f"corpus_selected_{item_id}", None)
                    st.session_state["confirm_delete_selected"] = False
                if warn_cols[2].button("取消", use_container_width=True):
                    st.session_state["confirm_delete_selected"] = False

    item_by_id = {item.id: item for item in items}
    editing_id = st.session_state.get("editing_corpus_id")
    if editing_id in item_by_id:
        item = item_by_id[editing_id]
        with st.expander("编辑语料", expanded=True):
            with st.form(f"edit_{item.id}"):
                edit_cols = st.columns([1, 1], gap="medium")
                new_title = edit_cols[0].text_input("标题", value=item.title)
                new_location = edit_cols[1].text_input("位置/引用名", value=item.location)
                new_tags = st.text_input("Tag", value=tag_text(item.tags), help="多个 Tag 用逗号分隔")
                new_text = st.text_area("内容", value=item.text, height=220)
                save_col, cancel_col = st.columns([1, 1])
                save_clicked = save_col.form_submit_button("保存修改", type="primary")
                cancel_clicked = cancel_col.form_submit_button("取消")
            if save_clicked:
                try:
                    pipeline.update_corpus(item.id, new_title, new_text, new_location, tags=parse_tags(new_tags))
                    queue_ui_notice("success", "已保存修改。请重建向量索引后用于问答。")
                    st.session_state.pop("editing_corpus_id", None)
                except ValueError as exc:
                    st.error(str(exc))
            if cancel_clicked:
                st.session_state.pop("editing_corpus_id", None)

with tab_import:
    st.subheader("导入文件")
    job_state = read_job_state(cfg)
    job_running = is_subscription_job_running(cfg)
    coordinator_state = read_coordinator_state(cfg)
    task_busy = bool(coordinator_state.active_kind)

    def display_subscription_status(subscription: TencentDocSubscription) -> str:
        raw_status = str(subscription.last_status or "")
        if "更新中" in raw_status:
            return "更新中"
        if "失败" in raw_status:
            return "失败"
        if "待解析" in raw_status:
            return "已下载待解析"
        if "跳过" in raw_status:
            return "已同步"
        if "成功" in raw_status or "已同步" in raw_status:
            return "已同步"
        if subscription.last_modified:
            return "待同步"
        return raw_status or "待同步"

    def edited_subscription_items(rows: list[dict]) -> tuple[list[TencentDocSubscription], list[str]]:
        result: list[TencentDocSubscription] = []
        errors: list[str] = []
        for index, row in enumerate(rows, start=1):
            name = str(row.get("名称", "")).strip()
            url = str(row.get("腾讯文档地址", "")).strip()
            if not name or not url:
                errors.append(f"第 {index} 行订阅必须填写名称和腾讯文档地址。")
                continue
            result.append(
                TencentDocSubscription(
                    name=name,
                    url=url,
                    tags=parse_tags(str(row.get("Tag", ""))),
                    enabled=bool(row.get("", True)),
                    last_modified=display_datetime(row.get("最后修改", "")),
                    last_status=str(row.get("状态", "") or ""),
                )
            )
        return result, errors

    def subscription_rows_from_items(subscriptions: list[TencentDocSubscription]) -> list[dict]:
        return [
            {
                "": subscription.enabled,
                "名称": subscription.name,
                "最后修改": display_datetime(subscription.last_modified),
                "状态": display_subscription_status(subscription),
                "腾讯文档地址": subscription.url,
                "Tag": tag_text(subscription.tags),
            }
            for subscription in subscriptions
        ]

    def subscription_table_height(row_count: int) -> int:
        return min(360, 54 + max(4, min(row_count, 8)) * 40)

    def render_subscription_dataframe(rows: list[dict]) -> None:
        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            use_container_width=True,
            height=subscription_table_height(len(rows)),
            column_order=["", "名称", "最后修改", "状态", "腾讯文档地址", "Tag"],
        )

    left, right = st.columns([0.55, 0.45], gap="large")
    with left:
        render_subscription_header(LOCAL_TASK_API_URL)
        st.markdown('<div class="import-action-style"></div>', unsafe_allow_html=True)
        start_col, add_col, save_col = st.columns([1.1, 0.9, 0.9], gap="small")
        start_label = "后台更新中..." if job_running else "开始后台更新"
        start_clicked = start_col.button(
            start_label,
            use_container_width=True,
            disabled=task_busy,
            key="subscription_start",
        )
        add_clicked = add_col.button("增加订阅", use_container_width=True, disabled=task_busy, key="subscription_add")
        save_clicked = save_col.button("保存订阅", use_container_width=True, disabled=task_busy, key="subscription_save")

        subscriptions = load_subscriptions(subscriptions_path())
        base_subscription_rows = subscription_rows_from_items(subscriptions)
        if "import_subscription_rows" not in st.session_state:
            st.session_state["import_subscription_rows"] = base_subscription_rows
        if "import_subscription_editor_version" not in st.session_state:
            st.session_state["import_subscription_editor_version"] = 0
        subscription_rows = list(st.session_state["import_subscription_rows"])
        if add_clicked:
            batch_add_subscription_dialog()

        if task_busy:
            render_subscription_dataframe(base_subscription_rows)
            edited_subscription_records = base_subscription_rows
        else:
            edited_subscriptions = st.data_editor(
                pd.DataFrame(subscription_rows),
                key=f"tencent_doc_subscriptions_v2_{st.session_state['import_subscription_editor_version']}",
                hide_index=True,
                use_container_width=True,
                height=subscription_table_height(len(subscription_rows)),
                num_rows="fixed",
                disabled=["最后修改", "状态"],
                column_order=["", "名称", "最后修改", "状态", "腾讯文档地址", "Tag"],
                column_config={
                    "": st.column_config.CheckboxColumn("", width="small"),
                    "名称": st.column_config.TextColumn("名称", width="medium"),
                    "最后修改": st.column_config.TextColumn("最后修改", width="small"),
                    "状态": st.column_config.TextColumn("状态", width="small"),
                    "腾讯文档地址": st.column_config.TextColumn("腾讯文档地址", width="large"),
                    "Tag": st.column_config.TextColumn("Tag", width="small"),
                },
            )
            edited_subscription_records = edited_subscriptions.to_dict("records")
        st.session_state["import_subscription_rows"] = edited_subscription_records
        current_subscriptions, subscription_errors = edited_subscription_items(edited_subscription_records)

        if start_clicked:
            if subscription_errors:
                for error in subscription_errors:
                    st.warning(error)
            else:
                save_subscriptions(subscriptions_path(), current_subscriptions)
                enabled_subscriptions = [subscription for subscription in current_subscriptions if subscription.enabled]
                if not enabled_subscriptions:
                    st.info("请先启用至少一个订阅。")
                else:
                    cookie = load_saved_cookie(cfg)
                    if not cookie:
                        start_cookie_login(cfg)
                        st.warning("尚未获取 Cookie，已打开腾讯文档登录页；获取成功后请再次开始更新。")
                    else:
                        result = start_subscription_job(
                            cfg,
                            subscriptions_path(),
                            enabled_subscriptions,
                            cookie,
                            origin="web",
                        )
                        if result.status == "busy":
                            st.warning(result.message)
                        else:
                            remember_recent_raw_files([])
                            job_running = True

        if save_clicked:
            if subscription_errors:
                for error in subscription_errors:
                    st.warning(error)
            else:
                save_subscriptions(subscriptions_path(), current_subscriptions)
                st.success("订阅已保存。")

        render_subscription_task_panel(LOCAL_TASK_API_URL)

    with right:
        st.markdown("### 上传文件")
        uploaded_files = st.file_uploader(
            "选择文件",
            type=[suffix.removeprefix(".") for suffix in sorted(SUPPORTED_SUFFIXES)],
            accept_multiple_files=True,
            label_visibility="collapsed",
        )
        st.markdown('<div class="upload-tag-row">&nbsp;</div>', unsafe_allow_html=True)
        upload_tag_col, upload_button_col = st.columns([0.68, 0.32], gap="small")
        upload_tag_col.caption("总体 Tag")
        shared_tags = upload_tag_col.text_input(
            "总体 Tag",
            placeholder="例如：618, 小家电, 迷住专属",
            label_visibility="collapsed",
        )
        upload_clicked = upload_button_col.button(
            "导入数据",
            use_container_width=True,
            disabled=not uploaded_files or task_busy,
            key="upload_import",
        )

        if uploaded_files:
            upload_list_height = min(330, 82 + max(2, min(len(uploaded_files), 5)) * 56)
            with st.container(height=upload_list_height):
                for index, uploaded_file in enumerate(uploaded_files):
                    name_col, status_col, tag_col, remove_col = st.columns([0.34, 0.16, 0.42, 0.08], gap="small")
                    name_col.write(uploaded_file.name)
                    status_col.caption("已上传")
                    tag_col.text_input(
                        f"{uploaded_file.name} 的 Tag",
                        placeholder="单独 Tag",
                        key=uploaded_file_tag_key(index, uploaded_file.name, uploaded_file.size),
                        label_visibility="collapsed",
                    )
                    remove_col.caption("×")
        else:
            st.info("选择文件后，可在列表中为每个文件设置单独 Tag。")

        if upload_clicked and uploaded_files:
            saved_paths = save_uploaded_files(uploaded_files)
            try:
                default_tags = parse_tags(shared_tags)
                path_tags = []
                for index, (saved_path, uploaded_file) in enumerate(zip(saved_paths, uploaded_files)):
                    file_tag_text = st.session_state.get(
                        uploaded_file_tag_key(index, uploaded_file.name, uploaded_file.size),
                        "",
                    )
                    file_tags = parse_tags(file_tag_text) if str(file_tag_text).strip() else default_tags
                    path_tags.append((saved_path, file_tags))
                with activity(cfg, "manual_upload", uuid4().hex):
                    stats = pipeline.ingest_files_with_tags(path_tags)
                remember_recent_raw_files(saved_paths)
                st.success(f"完成：解析 {stats['documents']} 个文档单元，新增 {stats['items']} 条语料")
                if stats.get("index_error"):
                    st.warning(f"语料已导入，但索引暂未重建：{stats['index_error']}")
                else:
                    st.success(f"索引已重建：{stats['chunks']} 个片段")
            except RuntimeError as exc:
                st.error(str(exc))

with tab_prompt:
    st.subheader("Prompt 设置")
    left, right = st.columns([0.68, 0.32], gap="large")

    with left:
        st.markdown("#### 系统提示词")
        st.caption("保存后会立即用于下一次提问。")
        with st.form("prompt_settings_form"):
            edited_prompt = st.text_area("Prompt", value=current_system_prompt(), height=360, label_visibility="collapsed")
            save_prompt = st.form_submit_button("保存 Prompt", type="primary")
        if save_prompt:
            save_system_prompt(edited_prompt)
            queue_ui_notice("success", "Prompt 已保存，并已立即生效。")

    with right:
        st.markdown("#### 本地模型")
        llama_plan = build_llama_server_plan(cfg)
        llama_status = "已启动" if is_llama_server_healthy(cfg) else "未启动"
        st.caption(f"后端：`{cfg.llm.backend}`")
        st.caption(f"llama.cpp server：`{llama_status}` `{cfg.llm.llama_server_url}`")
        st.caption(f"llama.cpp 后端选择：`{llama_plan.backend}`")
        if not llama_plan.enabled and cfg.llm.backend in {"llama_cpp_server", "auto"}:
            st.warning(llama_plan.reason)
        st.caption(f"Ollama：`{cfg.llm.ollama_model}`")
        st.caption(f"GGUF：`{cfg.llm_model_path}`")
        st.caption(f"上下文 / 输出：`{cfg.llm.n_ctx}` / `{cfg.llm.max_tokens}`")
        st.caption(f"线程 / 批大小 / GPU 层：`{cfg.llm.n_threads}` / `{cfg.llm.num_batch}` / `{cfg.llm.n_gpu_layers}`")
        st.caption(f"keep_alive：`{cfg.llm.keep_alive}`")
        st.divider()
        if st.button("恢复默认 Prompt", use_container_width=True):
            save_system_prompt(DEFAULT_SYSTEM_PROMPT)
            queue_ui_notice("success", "已恢复默认 Prompt。")

with tab_categories:
    st.subheader("商品类目")
    st.caption("维护商品类目、同义词和在售品牌。解析数据时会按品类自动汇总品牌，用于实时话术识别品牌/品类。")
    current_brand_map = category_brands()

    category_rows = st.data_editor(
        pd.DataFrame(category_alias_rows()),
        hide_index=True,
        use_container_width=True,
        num_rows="dynamic",
        height=460,
        column_config={
            "标准类目": st.column_config.TextColumn(
                "标准类目",
                help="例如：刀具、床、电饭煲",
                required=True,
            ),
            "同义词": st.column_config.TextColumn(
                "同义词",
                help="用逗号、顿号、分号或空格分隔。例如：菜刀、刀、切片工具",
            ),
            "在售品牌数": st.column_config.TextColumn(
                "在售品牌数",
                disabled=True,
                width="small",
            ),
            "品牌示例": st.column_config.TextColumn(
                "品牌示例",
                disabled=True,
                help="只显示少量品牌，避免长文本拖慢页面。完整品牌请在下方选择类目编辑。",
            ),
        },
        key="category_alias_editor",
    )

    edited_rows = category_rows.fillna("").to_dict("records") if isinstance(category_rows, pd.DataFrame) else []
    editable_categories = [str(row.get("标准类目", "") or "").strip() for row in edited_rows if str(row.get("标准类目", "") or "").strip()]
    brand_edit_category = st.selectbox(
        "编辑某个类目的完整在售品牌",
        editable_categories,
        index=0 if editable_categories else None,
        placeholder="选择类目",
        key="category_brand_edit_category",
    )
    brand_edit_text = ""
    if brand_edit_category:
        brand_edit_text = st.text_area(
            "该类目在售品牌",
            value="、".join(current_brand_map.get(brand_edit_category, [])),
            height=96,
            help="用顿号、逗号、分号或换行分隔。主表不会直接渲染这段长文本。",
            key=f"category_brand_detail_{brand_edit_category}",
        )

    action_col, hint_col = st.columns([0.18, 0.82], gap="medium")
    if action_col.button("保存类目", type="primary", use_container_width=True):
        aliases, brands, category_errors = category_alias_rows_to_config(edited_rows, current_brand_map)
        if brand_edit_category:
            brands[brand_edit_category] = parse_tags(brand_edit_text)
        if category_errors:
            for error in category_errors:
                st.warning(error)
        else:
            save_category_catalog(aliases, brands)
            get_pipeline.clear()
            queue_ui_notice("success", "商品类目已保存，下一次提问会使用新的类目和同义词。")
    hint_col.caption("新增类目：直接在表格底部新增一行；删除类目：清空该行标准类目或用表格自带删除行操作。在售品牌只在下方按单个类目编辑。")
