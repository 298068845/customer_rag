from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx


if get_script_run_ctx() is None:
    subprocess.run([sys.executable, "-m", "streamlit", "run", __file__], check=False)
    sys.exit()

from customer_rag.config import load_config
from customer_rag.loaders import SUPPORTED_SUFFIXES
from customer_rag.pipeline import RagPipeline


st.set_page_config(page_title="本地腾讯文档 RAG", layout="wide")
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
    </style>
    """,
    unsafe_allow_html=True,
)

DEFAULT_SYSTEM_PROMPT = """你是企业内部商品知识库助手。请只根据给定资料回答问题，不要编造。
如果用户询问商品、品类、型号或推荐清单，必须按下面格式逐个列出商品：

1. 商品名称（资料编号）
   - 型号/规格：
   - 下单流程：
   - 权益：
   - 商品链接：
   - 确认收货后：

要求：
- 每个字段都必须出现。
- 资料没有明确写出的字段，填写“资料中未找到”。
- 商品链接必须使用资料里的原始链接，不要改写。
- 不要编造特色、适合人群、价格、优惠或赠品，不需要单独列出品牌名。
- 最多回答 5 个最相关商品。"""


@st.cache_resource
def get_pipeline() -> RagPipeline:
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


def existing_images(paths: list[str]) -> list[str]:
    return [path for path in paths if Path(path).exists()]


def render_images(paths: list[str], limit: int = 3) -> None:
    images = existing_images(paths)[:limit]
    if images:
        st.image(images, width=180)


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
    try:
        relative = path.relative_to(cfg.raw_data_dir)
    except ValueError:
        return False
    return "_assets" not in relative.parts


def prompt_settings_path() -> Path:
    return cfg.index_dir / "prompt_settings.json"


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


def rebuild_index_button(key: str, label: str = "重建向量索引") -> None:
    if st.button(label, use_container_width=True, key=key):
        with st.spinner("正在重建索引..."):
            try:
                chunk_count = pipeline.rebuild_index()
                st.success(f"索引已重建：{chunk_count} 个片段")
            except RuntimeError as exc:
                st.error(str(exc))


@st.dialog("确认重建索引", width="small")
def confirm_rebuild_index_dialog() -> None:
    st.warning("重建向量索引可能需要一些时间，确认现在执行？")
    st.caption("取消请直接关闭弹窗。")
    _, confirm_col, _ = st.columns([1, 2, 1])
    if confirm_col.button("确认重建", type="primary", use_container_width=True):
        with st.spinner("正在重建索引..."):
            try:
                chunk_count = pipeline.rebuild_index()
                st.success(f"索引已重建：{chunk_count} 个片段")
            except RuntimeError as exc:
                st.error(str(exc))


def rebuild_raw_button(key: str) -> None:
    if st.button("重新解析全部原始文件", use_container_width=True, key=key):
        with st.spinner("正在重新解析 data/raw 并重建语料库..."):
            try:
                stats = pipeline.rebuild_corpus_from_raw()
                st.success(f"完成：解析 {stats['documents']} 个文档单元，语料 {stats['items']} 条")
                if stats.get("index_error"):
                    st.warning(f"语料已重建，但索引暂未重建：{stats['index_error']}")
                else:
                    st.success(f"索引已重建：{stats['chunks']} 个片段")
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))


cfg = load_config()
pipeline = get_pipeline()
system_prompt = load_system_prompt()

items = pipeline.list_corpus()
tag_options = sorted({tag for item in items for tag in item.tags})

st.title("本地腾讯文档 RAG")
st.caption("离线语料管理、向量检索和本地大模型问答")

tab_qa, tab_corpus, tab_import, tab_prompt = st.tabs(["问答", "语料管理", "导入文件", "Prompt 设置"])

with tab_qa:
    left, right = st.columns([0.28, 0.72], gap="large")

    with left:
        st.subheader("检索范围")
        qa_tags = st.multiselect("按 Tag 限定", tag_options, placeholder="默认检索全部语料")
        st.caption(f"当前最多引用 {cfg.top_k} 条资料。")
        st.divider()
        st.metric("语料总数", len(items))
        st.metric("Tag 数量", len(tag_options))

    with right:
        st.subheader("知识库问答")
        if not items:
            st.info("当前语料库为空，请先在“导入文件”或“语料管理”中添加语料。")

        question = st.text_input("问题", placeholder="例如：有什么电饭煲？")
        if question:
            with st.spinner("正在检索并生成答案..."):
                result = pipeline.ask(question, system_prompt=system_prompt, tags=qa_tags)

            if result.warning:
                st.warning(result.warning)

            st.markdown("#### 答案")
            st.write(result.answer)

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

    items = pipeline.list_corpus()
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
                    st.success(f"已更新 {changed} 条语料的 Tag。请重建向量索引后用于按分类检索。")
                    st.rerun()
            if toolbar[5].button("删除已选", type="secondary", use_container_width=True):
                if not pending_selected_ids:
                    st.info("请先勾选语料。")
                else:
                    st.session_state["confirm_delete_selected"] = True
            if toolbar[7].button("重建索引", type="primary", use_container_width=True):
                confirm_rebuild_index_dialog()

            if st.session_state.get("show_add_corpus_form", False):
                with st.form("add_corpus_form", clear_on_submit=True):
                    title = st.text_input("标题", placeholder="例如：客户退款流程")
                    tags_input = st.text_input("Tag", placeholder="例如：小家电, 618, 电饭煲")
                    text = st.text_area("内容", height=150, placeholder="输入要加入知识库的文本...")
                    submitted = st.form_submit_button("保存新增", type="primary")
                if submitted:
                    try:
                        pipeline.add_corpus(title, text, tags=parse_tags(tags_input))
                        st.success("已新增语料。请重建向量索引后用于问答。")
                        st.session_state["show_add_corpus_form"] = False
                        st.rerun()
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
                    st.success(f"已删除 {removed} 条语料。请重建向量索引后用于问答。")
                    for item_id in selected_ids:
                        st.session_state.pop(f"corpus_selected_{item_id}", None)
                    st.session_state["confirm_delete_selected"] = False
                    st.rerun()
                if warn_cols[2].button("取消", use_container_width=True):
                    st.session_state["confirm_delete_selected"] = False
                    st.rerun()

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
                    st.success("已保存修改。请重建向量索引后用于问答。")
                    st.session_state.pop("editing_corpus_id", None)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            if cancel_clicked:
                st.session_state.pop("editing_corpus_id", None)
                st.rerun()

with tab_import:
    st.subheader("导入文件")
    left, right = st.columns([0.45, 0.55], gap="large")

    with left:
        st.markdown("#### 上传并导入")
        st.caption("支持从腾讯文档导出的 docx、xlsx、csv、pdf、txt、md。文件会保存到 data/raw/。")
        uploaded_files = st.file_uploader(
            "选择文件",
            type=[suffix.removeprefix(".") for suffix in sorted(SUPPORTED_SUFFIXES)],
            accept_multiple_files=True,
        )
        shared_tags = st.text_input(
            "总体 Tag",
            placeholder="例如：618, 小家电, 迷住专属",
        )
        st.caption("总体 Tag 会作为默认值。右侧某个文件填写了自己的 Tag 时，该文件优先使用右侧 Tag。")

        if uploaded_files:
            st.markdown("#### 待导入文件")
            for uploaded_file in uploaded_files:
                st.caption(f"{uploaded_file.name} · {uploaded_file.size / 1024:.1f} KB")

        if uploaded_files and st.button("保存并导入", type="primary", use_container_width=True):
            saved_paths = save_uploaded_files(uploaded_files)
            with st.spinner("正在解析文件、写入语料库并重建索引..."):
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
                    stats = pipeline.ingest_files_with_tags(path_tags)
                    st.success(f"完成：解析 {stats['documents']} 个文档单元，新增 {stats['items']} 条语料")
                    if stats.get("index_error"):
                        st.warning(f"语料已导入，但索引暂未重建：{stats['index_error']}")
                    else:
                        st.success(f"索引已重建：{stats['chunks']} 个片段")
                    st.rerun()
                except RuntimeError as exc:
                    st.error(str(exc))

    with right:
        if uploaded_files:
            st.markdown("#### 文件 Tag 设置")
            st.caption("不填写则使用左侧总体 Tag；填写后该文件只使用这里的 Tag。")
            for index, uploaded_file in enumerate(uploaded_files):
                cols = st.columns([0.46, 0.54], gap="medium")
                cols[0].caption(f"{uploaded_file.name} · {uploaded_file.size / 1024:.1f} KB")
                cols[1].text_input(
                    f"{uploaded_file.name} 的 Tag",
                    placeholder="例如：618, 小家电",
                    key=uploaded_file_tag_key(index, uploaded_file.name, uploaded_file.size),
                    label_visibility="collapsed",
                )
            st.divider()

        st.markdown("#### 原始文件")
        raw_actions = st.columns([1, 1], gap="medium")
        with raw_actions[0]:
            rebuild_raw_button("import_rebuild_raw")
        with raw_actions[1]:
            rebuild_index_button("import_rebuild_index")

        raw_files = (
            sorted([path for path in cfg.raw_data_dir.glob("**/*") if path.is_file() and is_user_source_file(path)])
            if cfg.raw_data_dir.exists()
            else []
        )
        if raw_files:
            for path in raw_files:
                rel = path.relative_to(cfg.raw_data_dir)
                size_kb = path.stat().st_size / 1024
                st.caption(f"{rel} · {size_kb:.1f} KB")
        else:
            st.info("暂无文件。")

with tab_prompt:
    st.subheader("Prompt 设置")
    left, right = st.columns([0.68, 0.32], gap="large")

    with left:
        st.markdown("#### 系统提示词")
        st.caption("保存后会立即用于下一次提问。")
        with st.form("prompt_settings_form"):
            edited_prompt = st.text_area("Prompt", value=system_prompt, height=360, label_visibility="collapsed")
            save_prompt = st.form_submit_button("保存 Prompt", type="primary")
        if save_prompt:
            save_system_prompt(edited_prompt)
            st.success("Prompt 已保存。下一次提问会使用新的设置。")
            st.rerun()

    with right:
        st.markdown("#### 本地模型")
        st.caption(f"Ollama：`{cfg.llm.ollama_model}`")
        st.caption(f"GGUF：`{cfg.llm_model_path}`")
        st.caption(f"上下文 / 输出：`{cfg.llm.n_ctx}` / `{cfg.llm.max_tokens}`")
        st.caption(f"线程 / 批大小：`{cfg.llm.n_threads}` / `{cfg.llm.num_batch}`")
        st.caption(f"keep_alive：`{cfg.llm.keep_alive}`")
        st.divider()
        if st.button("恢复默认 Prompt", use_container_width=True):
            save_system_prompt(DEFAULT_SYSTEM_PROMPT)
            st.success("已恢复默认 Prompt。")
            st.rerun()
