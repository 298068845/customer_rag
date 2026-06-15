from __future__ import annotations

import json
import importlib
from dataclasses import replace
from datetime import datetime

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from customer_rag.category_config import category_brands
import customer_rag.talk_rag as talk_rag_module

talk_rag_module = importlib.reload(talk_rag_module)

from customer_rag.talk_rag import (
    FIXED_TALK_TITLES,
    BrandReplyRule,
    BrandSaleStatusRule,
    FixedReplyRule,
    FixedTalkEntry,
    RealtimeTalkConfig,
    SaleStatus,
    TalkRagEngine,
    TalkRagStore,
    clean_terms,
    new_id,
)


st.set_page_config(page_title="话术 RAG 管理台", layout="wide")

STORE = TalkRagStore()
ENGINE = TalkRagEngine(STORE)
STORE.ensure_seed_data()

SALE_STATUSES: list[SaleStatus] = ["售卖中", "暂时截团", "永久截团"]
DELETE_COLUMN_WIDTH = 78
TALK_ENTRY_OPTIONS = [
    "实时话术",
    *FIXED_TALK_TITLES,
]


st.markdown(
    """
    <style>
    .stApp { background: #ffffff; color: #303544; }
    .talk-card {
        background: #fff;
        border: 1px solid #d9dee8;
        border-radius: 8px;
        padding: 16px 18px;
        min-height: 68px;
    }
    .asset-list-item {
        background: #fff;
        border: 1px solid #d9dee8;
        border-radius: 8px;
        padding: 7px 12px;
        min-height: 38px;
        line-height: 22px;
        display: flex;
        align-items: center;
        margin-bottom: 0;
        transform: translateY(-8px);
    }
    .chain-step {
        background: #f7fbff;
        border: 1px solid #cfe0ff;
        border-radius: 8px;
        padding: 9px 11px;
        margin-bottom: 8px;
        color: #334155;
    }
    .section-note {
        color: #8a94a6;
        font-size: 13px;
        line-height: 1.7;
        margin: -2px 0 12px 0;
    }
    .compact-transfer-title {
        font-size: 20px;
        font-weight: 700;
        color: #303544;
        margin: 0 0 4px 0;
        line-height: 1.25;
    }
    .compact-transfer-note {
        color: #8a94a6;
        font-size: 13px;
        margin: 0 0 8px 0;
        line-height: 1.35;
    }
    div[data-testid="stCheckbox"] {
        min-height: 28px;
    }
    div[data-testid="stCheckbox"] label {
        margin-bottom: 0;
    }
    div[data-testid="stFileUploader"]:has(input[accept*=".zip"]) {
        margin: 0;
    }
    div[data-testid="stFileUploader"]:has(input[accept*=".zip"]) section[data-testid="stFileUploaderDropzone"] {
        min-height: 38px;
        height: 38px;
        padding: 0;
        border: 1px solid #d9dee8;
        border-radius: 8px;
        background: #ffffff;
        display: flex;
        align-items: center;
        justify-content: center;
    }
    div[data-testid="stFileUploader"]:has(input[accept*=".zip"]) section[data-testid="stFileUploaderDropzone"] > div {
        width: 100%;
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 0;
    }
    div[data-testid="stFileUploader"]:has(input[accept*=".zip"]) div[data-testid="stFileUploaderDropzoneInstructions"] {
        display: none !important;
        visibility: hidden !important;
        height: 0 !important;
        min-height: 0 !important;
        overflow: hidden !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    div[data-testid="stFileUploader"]:has(input[accept*=".zip"]) div[data-testid="stFileUploaderDropzoneInstructions"] * {
        display: none !important;
    }
    div[data-testid="stFileUploader"]:has(input[accept*=".zip"]) button[data-testid="stBaseButton-secondary"] {
        width: 100%;
        height: 36px;
        min-height: 36px;
        margin: 0;
        border: 0;
        background: transparent;
        box-shadow: none;
        color: #4b5563;
    }
    div[data-testid="stFileUploader"]:has(input[accept*=".zip"]) button[data-testid="stBaseButton-secondary"] > div {
        display: none;
    }
    div[data-testid="stFileUploader"]:has(input[accept*=".zip"]) button[data-testid="stBaseButton-secondary"]::after {
        content: "导入 ZIP";
        font-size: 14px;
        font-weight: 400;
    }
    div[data-testid="stButton"] > button {
        border-radius: 8px;
        min-height: 38px;
    }
    div[data-testid="stDownloadButton"] > button {
        border-radius: 8px;
        min-height: 38px;
    }
    div[data-testid="stButton"] > button[kind="primary"] {
        background: #ee5a55;
        border-color: #ee5a55;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def main() -> None:
    title_col, action_col = st.columns([0.45, 0.55], gap="large", vertical_alignment="top")
    with title_col:
        st.title("话术 RAG 管理台")
        st.caption("实时话术规则、品牌/品类识别和开团日期知识维护。")
    with action_col:
        render_config_transfer()

    tab_test, tab_realtime, tab_fixed = st.tabs(["对话测试", "实时话术", "固定话术"])
    with tab_test:
        render_match_test()
    with tab_realtime:
        render_realtime_talk()
    with tab_fixed:
        render_fixed_talk()


def render_config_transfer() -> None:
    st.markdown("<div class='compact-transfer-title'>配置导入导出</div>", unsafe_allow_html=True)
    include_realtime, selected_fixed_titles = render_config_transfer_scope()
    export_name = f"talk-rag-config-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    action_cols = st.columns([0.28, 0.28, 0.44], gap="small", vertical_alignment="bottom")
    with action_cols[0]:
        st.download_button(
            "导出配置 ZIP",
            data=STORE.export_config_zip(include_realtime=include_realtime, fixed_titles=selected_fixed_titles),
            file_name=export_name,
            mime="application/zip",
            use_container_width=True,
            help="导出实时话术、固定话术、品牌回复、开团日期、素材文案/图片和回复规则。",
            disabled=not include_realtime and not selected_fixed_titles,
        )
    with action_cols[1]:
        uploaded_zip = st.file_uploader(
            "导入 ZIP",
            type=["zip"],
            accept_multiple_files=False,
            label_visibility="collapsed",
            key="talk_rag_config_import_zip",
        )
    with action_cols[2]:
        if uploaded_zip is not None and st.button(
            "确认导入并覆盖所选板块",
            type="primary",
            use_container_width=True,
            disabled=not include_realtime and not selected_fixed_titles,
        ):
            try:
                summary = STORE.import_config_zip(
                    uploaded_zip.getvalue(),
                    include_realtime=include_realtime,
                    fixed_titles=selected_fixed_titles,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                clear_talk_config_editor_cache()
                st.success(
                    f"导入完成：实时话术 {'已覆盖' if summary['imported_realtime'] else '未覆盖'}，"
                    f"固定话术覆盖 {summary['imported_fixed_entries']} 个模块，"
                    f"素材导入 {summary['imported_assets']} 个。"
                )
                st.rerun()


def render_config_transfer_scope() -> tuple[bool, list[str]]:
    st.markdown("<div class='compact-transfer-note'>勾选导入/导出范围，默认全部板块。</div>", unsafe_allow_html=True)
    options = ["实时话术", *FIXED_TALK_TITLES]
    selected_fixed_titles: list[str] = []
    include_realtime = False
    option_cols = st.columns(4, gap="small")
    for index, title in enumerate(options):
        with option_cols[index % 4]:
            checked = st.checkbox(title, value=True, key=f"config_transfer_scope_{title}")
        if title == "实时话术":
            include_realtime = checked
        elif checked:
            selected_fixed_titles.append(title)
    if not include_realtime and not selected_fixed_titles:
        st.warning("请至少勾选一个板块。")
    return include_realtime, selected_fixed_titles


def render_match_test() -> None:
    left, right = st.columns([0.64, 0.36], gap="large")
    with left:
        st.subheader("对话测试")
        selected_entry = st.selectbox("话术入口", TALK_ENTRY_OPTIONS, index=0)
        question = st.text_input("问题", value="今日清单是什么", placeholder="例如：美的还有卖吗？空气炸锅有吗？源氏木语什么时候开团？")
        result = ENGINE.ask(question or "", selected_entry)
        st.markdown("#### 输出结果")
        answer = result.answer
        st.markdown(f"<div class='talk-card'>{answer}</div>", unsafe_allow_html=True)
    with right:
        st.subheader("命中链路")
        chain = result.chain
        for step in chain:
            st.markdown(f"<div class='chain-step'>{step}</div>", unsafe_allow_html=True)


def render_realtime_talk() -> None:
    config = STORE.load_realtime_config()
    parsed_brand_options = parsed_brand_options_from_catalog(config)
    if not config.brand_reply_rules_initialized:
        config = replace(
            config,
            brand_reply_rules=default_brand_reply_rules(parsed_brand_options),
            brand_reply_rules_initialized=True,
        )
        STORE.save_realtime_config(config)
    brand_options = persisted_brand_options(config)

    st.subheader("一、今日清单")
    today_left, today_right = st.columns([0.32, 0.68], gap="large")
    with today_left:
        today_triggers_text = st.text_area("触发词 / 同义问法", value="\n".join(config.today_triggers), height=132)
        if st.button("保存今日清单触发词", type="primary", use_container_width=True):
            STORE.save_realtime_config(replace(config, today_triggers=clean_terms(today_triggers_text)))
            st.success("今日清单触发词已保存。")
    with today_right:
        today_template = st.text_area(
            "回复内容",
            value=config.today_template,
            height=80,
            help="支持变量：{date_m_d}、{date_ymd}",
        )
        st.caption("示例：@小助理 {date_m_d}清单")
        if st.button("保存今日清单回复内容", type="primary", use_container_width=True):
            STORE.save_realtime_config(
                replace(config, today_template=today_template.strip() or "@小助理 {date_m_d}清单")
            )
            st.success("今日清单回复内容已保存。")

    st.divider()
    st.subheader("二、品牌清单")
    st.markdown(
        "<div class='section-note'>品牌关键词自动关联商品类目页的“在售品牌”列；品类关键词自动关联“标准类目/同义词”，并回复该行全部在售品牌。</div>",
        unsafe_allow_html=True,
    )
    brand_left, brand_right = st.columns([0.42, 0.58], gap="large", vertical_alignment="top")
    with brand_left:
        st.markdown("##### 触发词 / 同义问法")
        st.caption("keyword 可命中品牌，也可命中商品类目。")
        brand_triggers_text = st.text_area(
            "触发词 / 同义问法",
            value="\n".join(config.brand_triggers),
            height=300,
            label_visibility="collapsed",
        )
        st.caption("每行一个触发问法；保存后仅更新触发规则，不影响右侧品牌清单。")
        if st.button("保存品牌清单触发词", type="primary", use_container_width=True):
            STORE.save_realtime_config(replace(config, brand_triggers=clean_terms(brand_triggers_text)))
            st.success("品牌清单触发词已保存。")
    with brand_right:
        st.markdown("##### 回复内容规则")
        st.caption("品牌清单已独立保存，不再随订阅解析结果自动变化；点击表格中的 Add row 可手动新增品牌。")
        editor_rows_key = "brand_reply_rules_editor_rows"
        if editor_rows_key not in st.session_state:
            st.session_state[editor_rows_key] = brand_reply_rules_to_rows(config.brand_reply_rules)
        with st.form("brand_reply_rules_form", clear_on_submit=False, border=False):
            st.data_editor(
                pd.DataFrame(st.session_state[editor_rows_key]),
                hide_index=True,
                use_container_width=True,
                height=300,
                num_rows="dynamic",
                column_config={
                    "品牌": st.column_config.TextColumn("品牌", required=True),
                    "回复内容": st.column_config.TextColumn("回复内容"),
                    "补充回复": st.column_config.TextColumn("补充回复"),
                },
                key="brand_reply_rules_editor",
            )
            st.caption("回复内容为空时视为没有该品牌，检索命中后会忽略；补充回复用 `---` 分隔发送。")
            save_brand_replies = st.form_submit_button(
                "保存回复内容规则",
                type="primary",
                use_container_width=True,
                on_click=save_brand_reply_editor,
            )
        saved_notice = st.session_state.pop("brand_reply_rules_saved", False)
        if save_brand_replies or saved_notice:
            st.toast("回复内容规则已保存。", icon="✅")

    st.divider()
    st.subheader("三、开团日期")
    open_left, open_mid, open_right = st.columns([0.32, 0.33, 0.35], gap="large")
    with open_left:
        open_group_triggers_text = st.text_area("触发词 / 同义问法", value="\n".join(config.open_group_triggers), height=184)
        if st.button("保存开团日期触发词", type="primary", use_container_width=True):
            STORE.save_realtime_config(replace(config, open_group_triggers=clean_terms(open_group_triggers_text)))
            st.success("开团日期触发词已保存。")
    with open_mid:
        st.markdown("##### 状态分流规则")
        st.caption("售卖中：回复“现在可以买。”，再调用第二项该品牌清单话术。")
        st.caption("暂时截团：读取开团日期知识，回复未来开团日期；再调用第二项该品牌清单话术；最后回复“先看看款式”。")
        st.caption("永久截团：回复“已经截团了不再卖了，看看别的。”")
    with open_right:
        st.markdown("##### 品牌售卖状态 List")
        sale_status_rows = st.data_editor(
            pd.DataFrame(sale_status_rules_to_rows(config.sale_status_rules, brand_options)),
            hide_index=True,
            use_container_width=True,
            height=184,
            column_config={
                "品牌": st.column_config.TextColumn("品牌", disabled=True),
                "售卖状态": st.column_config.SelectboxColumn("售卖状态", options=SALE_STATUSES, required=True),
            },
            key="sale_status_rules_editor",
        )
        if st.button("保存品牌售卖状态", type="primary", use_container_width=True):
            STORE.save_realtime_config(replace(config, sale_status_rules=rows_to_sale_status_rules(sale_status_rows)))
            st.success("品牌售卖状态已保存。")

    open_group_knowledge = st.text_area("开团日期知识内容", value=config.open_group_knowledge, height=170)
    if st.button("保存开团日期知识", type="primary", use_container_width=True):
        STORE.save_realtime_config(replace(config, open_group_knowledge=open_group_knowledge.strip()))
        st.success("开团日期知识已保存。")
    st.caption("品牌清单与售卖状态以当前保存配置为准；品类和品牌提问关联仍使用商品类目解析关系。")


def render_fixed_talk() -> None:
    entries = STORE.load_fixed_entries()
    assets = STORE.load_assets()
    title_col, _ = st.columns([0.38, 0.62], gap="large")
    with title_col:
        selected_title = st.selectbox("标题", FIXED_TALK_TITLES, key="fixed_talk_title")
    entry = next(item for item in entries if item.title == selected_title)
    scoped_assets = assets_for_title(assets, selected_title)

    st.subheader("固定话术规则")
    trigger_col, reply_col = st.columns([0.38, 0.62], gap="large", vertical_alignment="top")
    with trigger_col:
        st.markdown("##### 触发词 / 同义问法")
        triggers_text = st.text_area(
            "触发词 / 同义问法",
            value="\n".join(entry.triggers),
            height=260,
            label_visibility="collapsed",
            key=f"fixed_triggers_{selected_title}",
            help="每行一个问法，可使用 {keyword} 作为关键词占位符。",
        )
        if st.button("保存触发词", type="primary", use_container_width=True, key=f"save_fixed_triggers_{selected_title}"):
            save_fixed_entry(replace(entry, triggers=clean_terms(triggers_text)), entries)
            st.success("触发词已保存。")

    with reply_col:
        st.markdown("##### 回复内容规则")
        asset_options = [asset.title for asset in scoped_assets]
        asset_ids_by_title = {asset.title: asset.id for asset in scoped_assets}
        asset_titles_by_id = {asset.id: asset.title for asset in scoped_assets}
        editor_rows_key = f"fixed_rules_rows_{selected_title}"
        if editor_rows_key not in st.session_state:
            st.session_state[editor_rows_key] = fixed_reply_rules_to_rows(entry.reply_rules, asset_titles_by_id)
        editor_version_key = f"fixed_rules_editor_version_{selected_title}"
        editor_key = f"fixed_rules_{selected_title}_{st.session_state.get(editor_version_key, 0)}"
        with st.form(f"fixed_rules_form_{selected_title}", clear_on_submit=False, border=False):
            current_editor_rows = st.data_editor(
                fixed_reply_rules_dataframe(st.session_state[editor_rows_key]),
                hide_index=True,
                use_container_width=True,
                height=260,
                num_rows="add",
                column_config={
                    "删除": st.column_config.CheckboxColumn("删除", width=DELETE_COLUMN_WIDTH, help="勾选后保存会删除该行。"),
                    "关键词": st.column_config.TextColumn("关键词", width=260, help="可输入多个，用逗号分隔。", required=True),
                    "回复内容": st.column_config.MultiselectColumn(
                        "回复内容（从素材库选择）",
                        width=680,
                        options=asset_options,
                        help="同一行可添加多个素材，发送时使用 --- 分隔。",
                    ),
                },
                key=editor_key,
            )
            fixed_rule_visible_rows = dataframe_object_records(current_editor_rows)
            install_commit_before_submit_guard("保存回复内容规则")
            save_fixed_rules = st.form_submit_button(
                "保存回复内容规则",
                type="primary",
                use_container_width=True,
            )
        if save_fixed_rules:
            save_fixed_reply_rules_editor(
                selected_title,
                editor_rows_key,
                editor_key,
                editor_version_key,
                fixed_rule_visible_rows,
            )
        fixed_rules_error = st.session_state.pop(f"fixed_reply_rules_error_{selected_title}", "")
        if fixed_rules_error:
            st.error(fixed_rules_error)
        elif save_fixed_rules or st.session_state.pop("fixed_reply_rules_saved", False):
            st.toast("回复内容规则已保存。", icon="✅")

    st.divider()
    st.subheader("素材库管理")
    list_col, add_col = st.columns([0.38, 0.62], gap="large", vertical_alignment="top")
    with list_col:
        st.markdown("##### 素材库 List")
        if not scoped_assets:
            st.caption("暂无素材。")
        else:
            asset_rows_key = f"asset_list_editor_rows_{selected_title}"
            asset_ids_key = f"asset_list_editor_ids_{selected_title}"
            current_asset_ids = [asset.id for asset in scoped_assets]
            if (
                asset_rows_key not in st.session_state
                or st.session_state.get(asset_ids_key) != current_asset_ids
            ):
                st.session_state[asset_rows_key] = asset_items_to_rows(scoped_assets)
                st.session_state[asset_ids_key] = current_asset_ids
            asset_editor_version_key = f"asset_list_editor_version_{selected_title}"
            asset_editor_key = f"asset_list_editor_{selected_title}_{st.session_state.get(asset_editor_version_key, 0)}"
            current_asset_rows = st.data_editor(
                asset_list_dataframe(st.session_state[asset_rows_key]),
                hide_index=True,
                use_container_width=True,
                height=184,
                column_config={
                    "删除": st.column_config.CheckboxColumn("删除", width=DELETE_COLUMN_WIDTH, help="勾选后保存会删除素材。"),
                    "素材名称": st.column_config.TextColumn("素材名称", width=440, disabled=True),
                },
                key=asset_editor_key,
                on_change=sync_data_editor_rows,
                args=(asset_rows_key, asset_editor_key),
            )
            save_asset_list = st.button(
                "保存素材库 List",
                type="primary",
                use_container_width=True,
                key=f"save_asset_list_{selected_title}",
                on_click=save_asset_list_editor,
                args=(selected_title, asset_rows_key, asset_editor_key, asset_editor_version_key, asset_ids_key),
            )
            blocked_asset_delete_message = st.session_state.pop(f"asset_delete_blocked_message_{selected_title}", "")
            if blocked_asset_delete_message:
                st.error(blocked_asset_delete_message)
            elif save_asset_list or st.session_state.pop(f"asset_list_saved_{selected_title}", False):
                st.toast("素材库 List 已保存。", icon="✅")

    with add_col:
        st.markdown("##### 添加素材")
        asset_form_version_key = f"new_asset_form_version_{selected_title}"
        asset_form_version = st.session_state.get(asset_form_version_key, 0)
        name_key = f"new_asset_name_{selected_title}_{asset_form_version}"
        files_key = f"new_asset_files_{selected_title}_{asset_form_version}"
        copy_key = f"new_asset_copy_{selected_title}_{asset_form_version}"
        current_copy = str(st.session_state.get(copy_key, "")).strip()
        asset_name = st.text_input("名称", key=name_key)
        uploaded_files = st.file_uploader(
            "上传图片",
            type=["png", "jpg", "jpeg", "gif", "webp", "bmp"],
            accept_multiple_files=True,
            disabled=bool(current_copy),
            key=files_key,
        )
        asset_copy = st.text_area("文案", height=140, disabled=bool(uploaded_files), key=copy_key)
        if uploaded_files:
            st.caption("已上传图片，文案输入已禁用。")
        elif asset_copy.strip():
            st.caption("已输入文案，图片上传已禁用。")
        if st.button("保存素材", type="primary", use_container_width=True, key=f"save_new_asset_{selected_title}"):
            if not asset_name.strip():
                st.error("请填写素材名称。")
            elif not uploaded_files and not asset_copy.strip():
                st.error("请上传图片或填写文案。")
            elif any(asset.title == asset_name.strip() for asset in scoped_assets):
                st.error("素材名称不能重复。")
            else:
                STORE.save_uploaded_assets(uploaded_files, asset_name, [selected_title], asset_copy)
                st.success("素材已保存。")
                st.session_state[asset_form_version_key] = asset_form_version + 1
                clear_asset_list_editor_cache(selected_title)
                st.rerun()


def save_fixed_entry(entry: FixedTalkEntry, entries: list[FixedTalkEntry]) -> None:
    STORE.save_fixed_entries([entry if item.title == entry.title else item for item in entries])


def sync_data_editor_rows(rows_key: str, editor_key: str) -> None:
    st.session_state[rows_key] = apply_data_editor_changes_preserve_values(
        list(st.session_state.get(rows_key, [])),
        st.session_state.get(editor_key, {}),
    )


def save_fixed_reply_rules_editor(
    selected_title: str,
    editor_rows_key: str,
    editor_key: str,
    editor_version_key: str,
    visible_rows: list[dict[str, object]],
) -> None:
    st.session_state.pop("fixed_reply_rules_saved", None)
    st.session_state.pop(f"fixed_reply_rules_error_{selected_title}", None)
    assets = assets_for_title(STORE.load_assets(), selected_title)
    asset_ids_by_title = {asset.title: asset.id for asset in assets}
    asset_titles_by_id = {asset.id: asset.title for asset in assets}
    sync_data_editor_rows(editor_rows_key, editor_key)
    synced_rows = list(st.session_state.get(editor_rows_key, []))
    rows = prefer_rows_with_reply_assets(visible_rows, synced_rows)
    rows = [row for row in rows if not truthy_value(row.get("删除", False))]
    validation_errors = validate_fixed_reply_asset_bindings(rows, asset_ids_by_title)
    if validation_errors:
        st.session_state[f"fixed_reply_rules_error_{selected_title}"] = "\n".join(validation_errors)
        return

    rules = rows_to_fixed_reply_rules(rows, asset_ids_by_title)
    entries = STORE.load_fixed_entries()
    entry = next(item for item in entries if item.title == selected_title)
    save_fixed_entry(replace(entry, reply_rules=rules), entries)
    persisted_entry = next(item for item in STORE.load_fixed_entries() if item.title == selected_title)
    persisted_errors = validate_persisted_fixed_reply_rules(rules, persisted_entry.reply_rules)
    if persisted_errors:
        st.session_state[f"fixed_reply_rules_error_{selected_title}"] = "\n".join(persisted_errors)
        return

    st.session_state[editor_rows_key] = fixed_reply_rules_to_rows(rules, asset_titles_by_id)
    st.session_state[editor_version_key] = st.session_state.get(editor_version_key, 0) + 1
    st.session_state["fixed_reply_rules_saved"] = True

def save_asset_list_editor(
    selected_title: str,
    asset_rows_key: str,
    asset_editor_key: str,
    asset_editor_version_key: str,
    asset_ids_key: str,
) -> None:
    sync_data_editor_rows(asset_rows_key, asset_editor_key)
    stored_rows = list(st.session_state.get(asset_rows_key, []))
    rows = attach_asset_ids_to_rows(
        apply_data_editor_changes_preserve_values(stored_rows, st.session_state.get(asset_editor_key, {})),
        stored_rows,
    )
    delete_asset_ids = {
        str(row.get("素材ID", ""))
        for row in rows
        if truthy_value(row.get("删除", False)) and str(row.get("素材ID", ""))
    }
    if not delete_asset_ids:
        st.session_state[f"asset_list_saved_{selected_title}"] = True
        return

    assets = STORE.load_assets()
    referenced_assets = referenced_asset_titles(delete_asset_ids, STORE.load_fixed_entries(), assets)
    if referenced_assets:
        st.session_state[f"asset_delete_blocked_message_{selected_title}"] = (
            "素材已被回复内容引用，请先解除回复内容的绑定方可删除：" + "、".join(referenced_assets)
        )
        return

    for asset in assets:
        if asset.id in delete_asset_ids:
            STORE.delete_asset_files(asset)
            remove_asset_from_fixed_entries(asset.id)
    remaining_assets = [item for item in assets if item.id not in delete_asset_ids]
    STORE.save_assets(remaining_assets)
    clear_fixed_rule_editor_cache()
    remaining_scoped_assets = assets_for_title(remaining_assets, selected_title)
    st.session_state[asset_rows_key] = asset_items_to_rows(remaining_scoped_assets)
    st.session_state[asset_ids_key] = [asset.id for asset in remaining_scoped_assets]
    st.session_state[asset_editor_version_key] = st.session_state.get(asset_editor_version_key, 0) + 1
    st.session_state[f"asset_list_saved_{selected_title}"] = True


def fixed_reply_rules_to_rows(
    rules: list[FixedReplyRule],
    asset_titles_by_id: dict[str, str],
) -> list[dict[str, object]]:
    return [
        {
            "删除": False,
            "关键词": "，".join(rule.keywords),
            "回复内容": [asset_titles_by_id[item_id] for item_id in rule.asset_ids if item_id in asset_titles_by_id],
        }
        for rule in rules
    ]


def fixed_reply_rules_dataframe(rows: list[dict[str, object]], include_reply_content: bool = True) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for row in rows:
        record: dict[str, object] = {
            "删除": truthy_value(row.get("删除", False)),
            "关键词": str(row.get("关键词", "") or ""),
        }
        if include_reply_content:
            record["回复内容"] = normalize_option_like_list(row.get("回复内容", []))
        records.append(record)
    columns = ["删除", "关键词", "回复内容"] if include_reply_content else ["删除", "关键词"]
    dataframe = pd.DataFrame(records, columns=columns)
    dataframe["删除"] = dataframe["删除"].astype(bool)
    dataframe["关键词"] = dataframe["关键词"].astype("string")
    if include_reply_content:
        dataframe["回复内容"] = dataframe["回复内容"].astype(object)
    return dataframe


def asset_items_to_rows(assets: list) -> list[dict[str, object]]:
    return [{"删除": False, "素材名称": asset.title, "素材ID": asset.id} for asset in assets]


def asset_list_dataframe(rows: list[dict[str, object]]) -> pd.DataFrame:
    records = [
        {
            "删除": truthy_value(row.get("删除", False)),
            "素材名称": str(row.get("素材名称", "") or ""),
        }
        for row in rows
    ]
    dataframe = pd.DataFrame(records, columns=["删除", "素材名称"])
    dataframe["删除"] = dataframe["删除"].astype(bool)
    dataframe["素材名称"] = dataframe["素材名称"].astype("string")
    return dataframe


def normalize_option_like_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [item for item in (option_value_text(item) for item in value) if item]
    return clean_terms(str(value or "").strip("[](){}").replace("'", "").replace('"', ""))


def prefer_rows_with_reply_assets(
    visible_rows: list[dict[str, object]],
    synced_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not visible_rows:
        return synced_rows
    if not synced_rows:
        return visible_rows
    rows: list[dict[str, object]] = []
    max_len = max(len(visible_rows), len(synced_rows))
    for index in range(max_len):
        visible = visible_rows[index] if index < len(visible_rows) else {}
        synced = synced_rows[index] if index < len(synced_rows) else {}
        row = dict(synced or visible)
        if has_reply_assets(visible) and not has_reply_assets(row):
            row["回复内容"] = visible.get("回复内容", [])
        if str(visible.get("关键词", "")).strip():
            row["关键词"] = visible.get("关键词", "")
        if "删除" in visible:
            row["删除"] = visible.get("删除", False)
        rows.append(row)
    return rows


def has_reply_assets(row: dict[str, object]) -> bool:
    value = row.get("回复内容", [])
    if isinstance(value, (list, tuple, set)):
        return any(str(item).strip() for item in value)
    return bool(str(value or "").strip())


def assets_for_title(assets: list, title: str) -> list:
    return [asset for asset in assets if title in getattr(asset, "categories", [])]


def attach_asset_ids_to_rows(
    visible_rows: list[dict[str, object]],
    stored_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, row in enumerate(visible_rows):
        value = dict(row)
        if "素材ID" not in value and index < len(stored_rows):
            value["素材ID"] = stored_rows[index].get("素材ID", "")
        rows.append(value)
    return rows


def referenced_asset_titles(delete_asset_ids: set[str], entries: list[FixedTalkEntry], assets: list) -> list[str]:
    if not delete_asset_ids:
        return []
    asset_titles = {asset.id: asset.title for asset in assets}
    referenced_ids: set[str] = set()
    for entry in entries:
        for rule in entry.reply_rules:
            referenced_ids.update(asset_id for asset_id in rule.asset_ids if asset_id in delete_asset_ids)
    return [asset_titles.get(asset_id, asset_id) for asset_id in delete_asset_ids if asset_id in referenced_ids]


def rows_to_fixed_reply_rules(
    rows: list[dict[str, object]],
    asset_ids_by_title: dict[str, str],
) -> list[FixedReplyRule]:
    rules: list[FixedReplyRule] = []
    for row in rows:
        keywords = clean_terms(str(row.get("关键词", "")))
        selected_assets = normalize_selected_asset_titles(row.get("回复内容", []), asset_ids_by_title)
        asset_ids = [asset_ids_by_title[str(title)] for title in selected_assets if str(title) in asset_ids_by_title]
        if keywords:
            rules.append(FixedReplyRule(id=new_id(), keywords=keywords, asset_ids=asset_ids))
    return rules


def validate_fixed_reply_asset_bindings(
    rows: list[dict[str, object]],
    asset_ids_by_title: dict[str, str],
) -> list[str]:
    errors: list[str] = []
    available_assets = "、".join(asset_ids_by_title) or "无"
    for index, row in enumerate(rows, start=1):
        keywords = clean_terms(str(row.get("关键词", "")))
        raw_reply_assets = row.get("回复内容", [])
        if not keywords or not has_reply_assets(row):
            continue
        selected_assets = normalize_selected_asset_titles(raw_reply_assets, asset_ids_by_title)
        if selected_assets:
            continue
        errors.append(
            f"第 {index} 行“{','.join(keywords)}”的回复内容没有成功匹配到素材库。"
            f"原始值：{raw_reply_assets!r}；当前可选素材：{available_assets}"
        )
    return errors


def validate_persisted_fixed_reply_rules(
    expected_rules: list[FixedReplyRule],
    persisted_rules: list[FixedReplyRule],
) -> list[str]:
    errors: list[str] = []
    persisted_by_keywords = {tuple(rule.keywords): rule for rule in persisted_rules}
    for rule in expected_rules:
        persisted_rule = persisted_by_keywords.get(tuple(rule.keywords))
        if persisted_rule is None:
            errors.append(f"保存后未找到关键词规则：{','.join(rule.keywords)}")
            continue
        if list(rule.asset_ids) != list(persisted_rule.asset_ids):
            errors.append(
                f"关键词“{','.join(rule.keywords)}”保存后素材绑定不一致。"
                f"期望：{rule.asset_ids}；实际：{persisted_rule.asset_ids}"
            )
    return errors


def normalize_option_values(value: object, options: list[str]) -> list[str]:
    option_set = set(options)
    if isinstance(value, (list, tuple, set)):
        values = [str(item).strip() for item in value]
    else:
        values = clean_terms(str(value or "").strip("[](){}").replace("'", "").replace('"', ""))
    return [item for item in values if item in option_set]


def normalize_selected_asset_titles(value: object, asset_ids_by_title: dict[str, str]) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        values = [option_value_text(item) for item in value]
    elif isinstance(value, dict):
        values = [option_value_text(value)]
    else:
        text = str(value or "").strip()
        if not text:
            values = []
        elif text in asset_ids_by_title:
            values = [text]
        else:
            values = clean_terms(text.strip("[](){}").replace("'", "").replace('"', ""))
    return [item for item in values if item in asset_ids_by_title]


def option_value_text(value: object) -> str:
    if isinstance(value, dict):
        for key in ("value", "label", "title", "素材名称", "name"):
            text = str(value.get(key, "") or "").strip()
            if text:
                return text
        return ""
    return str(value or "").strip()


def dataframe_object_records(rows: object) -> list[dict[str, object]]:
    if not isinstance(rows, pd.DataFrame):
        return []
    records: list[dict[str, object]] = []
    for record in rows.to_dict("records"):
        cleaned: dict[str, object] = {}
        for key, value in record.items():
            cleaned[str(key)] = "" if is_missing_scalar(value) else value
        records.append(cleaned)
    return records


def install_commit_before_submit_guard(button_text: str) -> None:
    """Delay a submit click long enough for an active data_editor cell to commit."""
    components.html(
        f"""
        <script>
        (() => {{
            const parentDoc = window.parent.document;
            const targetText = {json.dumps(button_text, ensure_ascii=False)};

            function normalizedText(element) {{
                return (element.innerText || element.textContent || "").trim();
            }}

            function attachGuard() {{
                const buttons = Array.from(parentDoc.querySelectorAll("button"))
                    .filter((button) => normalizedText(button) === targetText);

                for (const button of buttons) {{
                    if (button.dataset.codexCommitBeforeSubmit === "1") {{
                        continue;
                    }}
                    button.dataset.codexCommitBeforeSubmit = "1";
                    button.addEventListener("click", (event) => {{
                        if (button.dataset.codexForwardedClick === "1") {{
                            button.dataset.codexForwardedClick = "0";
                            return;
                        }}

                        const active = parentDoc.activeElement;
                        const editorOverlayOpen = Boolean(
                            parentDoc.querySelector('[data-testid="stDataFrame"] input:focus, [data-testid="stDataFrame"] textarea:focus, [role="listbox"], [role="option"]')
                        );
                        const hasActiveEditor = editorOverlayOpen || (active && active !== parentDoc.body);
                        if (!hasActiveEditor) {{
                            return;
                        }}

                        event.preventDefault();
                        event.stopImmediatePropagation();
                        if (active && typeof active.blur === "function") {{
                            active.blur();
                        }}

                        window.setTimeout(() => {{
                            button.dataset.codexForwardedClick = "1";
                            button.click();
                        }}, 180);
                    }}, true);
                }}
            }}

            attachGuard();
            new MutationObserver(attachGuard).observe(parentDoc.body, {{
                childList: true,
                subtree: true,
            }});
        }})();
        </script>
        """,
        height=0,
    )


def is_missing_scalar(value: object) -> bool:
    if isinstance(value, (list, tuple, set, dict)):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def apply_data_editor_changes_preserve_values(
    base_rows: list[dict[str, object]],
    editor_state: object,
) -> list[dict[str, object]]:
    rows = [dict(row) for row in base_rows]
    if not isinstance(editor_state, dict):
        return rows

    edited_rows = editor_state.get("edited_rows", {})
    if isinstance(edited_rows, dict):
        for raw_index, changes in edited_rows.items():
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(rows) and isinstance(changes, dict):
                rows[index].update({str(key): value for key, value in changes.items()})

    deleted_rows = editor_state.get("deleted_rows", [])
    if isinstance(deleted_rows, list):
        deleted_indexes: set[int] = set()
        for value in deleted_rows:
            try:
                deleted_indexes.add(int(value))
            except (TypeError, ValueError):
                continue
        rows = [row for index, row in enumerate(rows) if index not in deleted_indexes]

    added_rows = editor_state.get("added_rows", [])
    if isinstance(added_rows, list):
        rows.extend({str(key): value for key, value in row.items()} for row in added_rows if isinstance(row, dict))
    return rows


def truthy_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y", "是"}


def remove_asset_from_fixed_entries(asset_id: str) -> None:
    entries = STORE.load_fixed_entries()
    STORE.save_fixed_entries(
        [
            replace(
                entry,
                reply_rules=[replace(rule, asset_ids=[item for item in rule.asset_ids if item != asset_id]) for rule in entry.reply_rules],
            )
            for entry in entries
        ]
    )


def clear_fixed_rule_editor_cache() -> None:
    for title in FIXED_TALK_TITLES:
        st.session_state.pop(f"fixed_rules_rows_{title}", None)


def clear_asset_list_editor_cache(title: str) -> None:
    st.session_state.pop(f"asset_list_editor_rows_{title}", None)
    st.session_state.pop(f"asset_list_editor_ids_{title}", None)


def clear_talk_config_editor_cache() -> None:
    st.session_state.pop("brand_reply_rules_editor_rows", None)
    st.session_state.pop("brand_reply_rules_editor", None)
    st.session_state.pop("sale_status_rules_editor", None)
    for title in FIXED_TALK_TITLES:
        st.session_state.pop(f"fixed_rules_rows_{title}", None)
        st.session_state.pop(f"asset_list_editor_rows_{title}", None)
        st.session_state.pop(f"asset_list_editor_ids_{title}", None)


def brand_reply_rules_to_rows(rules: list[BrandReplyRule]) -> list[dict[str, str]]:
    rules_by_brand = {
        rule.keyword: rule
        for rule in rules
        if rule.keyword_type == "品牌" and rule.keyword
    }
    brands = list(rules_by_brand)
    return [
        {
            "品牌": brand,
            "回复内容": "\n".join(rules_by_brand[brand].reply_terms),
            "补充回复": getattr(rules_by_brand[brand], "supplemental_reply", ""),
        }
        for brand in brands
    ]


def rows_to_brand_reply_rules(rows: pd.DataFrame, existing_rules: list[BrandReplyRule]) -> list[BrandReplyRule]:
    records = dataframe_records(rows)
    ids_by_brand = {rule.keyword: rule.id for rule in existing_rules if rule.keyword_type == "品牌" and rule.keyword}
    values: list[BrandReplyRule] = []
    seen: set[str] = set()
    for row in records:
        brand = str(row.get("品牌", "")).strip()
        replies = clean_terms(str(row.get("回复内容", "")).splitlines())
        supplemental_reply = str(row.get("补充回复", "")).strip()
        brand_key = brand.lower()
        if not brand or brand_key in seen:
            continue
        seen.add(brand_key)
        values.append(
            BrandReplyRule(
                id=ids_by_brand.get(brand, new_id()),
                keyword_type="品牌",
                keyword=brand,
                reply_terms=replies,
                supplemental_reply=supplemental_reply,
            )
        )
    return values


def dataframe_records(rows: pd.DataFrame) -> list[dict[str, str]]:
    if not isinstance(rows, pd.DataFrame):
        return []
    return [
        {str(key): str(value or "") for key, value in record.items()}
        for record in rows.fillna("").to_dict("records")
    ]


def save_brand_reply_editor() -> None:
    base_rows = list(st.session_state.get("brand_reply_rules_editor_rows", []))
    editor_state = st.session_state.get("brand_reply_rules_editor", {})
    rows = apply_data_editor_changes(base_rows, editor_state)
    latest_config = STORE.load_realtime_config()
    saved_rules = rows_to_brand_reply_rules(pd.DataFrame(rows), latest_config.brand_reply_rules)
    STORE.save_realtime_config(
        replace(
            latest_config,
            brand_reply_rules=saved_rules,
            brand_reply_rules_initialized=True,
        )
    )
    st.session_state["brand_reply_rules_saved"] = True


def apply_data_editor_changes(base_rows: list[dict[str, str]], editor_state: object) -> list[dict[str, str]]:
    rows = [dict(row) for row in base_rows]
    if not isinstance(editor_state, dict):
        return rows

    edited_rows = editor_state.get("edited_rows", {})
    if isinstance(edited_rows, dict):
        for raw_index, changes in edited_rows.items():
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(rows) and isinstance(changes, dict):
                rows[index].update({str(key): str(value or "") for key, value in changes.items()})

    deleted_rows = editor_state.get("deleted_rows", [])
    if isinstance(deleted_rows, list):
        deleted_indexes: set[int] = set()
        for value in deleted_rows:
            try:
                deleted_indexes.add(int(value))
            except (TypeError, ValueError):
                continue
        rows = [row for index, row in enumerate(rows) if index not in deleted_indexes]

    added_rows = editor_state.get("added_rows", [])
    if isinstance(added_rows, list):
        rows.extend(
            {str(key): str(value or "") for key, value in row.items()}
            for row in added_rows
            if isinstance(row, dict)
        )
    return rows


def sale_status_rules_to_rows(rules: list[BrandSaleStatusRule], brand_options: list[str]) -> list[dict[str, str]]:
    status_by_brand = {rule.brand: rule.status for rule in rules if rule.brand}
    brands = brand_options or [rule.brand for rule in rules if rule.brand]
    return [{"品牌": brand, "售卖状态": status_by_brand.get(brand, "售卖中")} for brand in brands]


def rows_to_sale_status_rules(rows: pd.DataFrame) -> list[BrandSaleStatusRule]:
    records = rows.fillna("").to_dict("records") if isinstance(rows, pd.DataFrame) else []
    values: list[BrandSaleStatusRule] = []
    for row in records:
        brand = str(row.get("品牌", "") or row.get("品牌名", "")).strip()
        if not brand:
            continue
        status = str(row.get("售卖状态", "售卖中")).strip()
        if status not in SALE_STATUSES:
            status = "售卖中"
        values.append(
            BrandSaleStatusRule(
                id=new_id(),
                brand=brand,
                aliases=[],
                status=status,  # type: ignore[arg-type]
            )
        )
    return values


def default_brand_reply_rules(brands: list[str]) -> list[BrandReplyRule]:
    return [
        BrandReplyRule(
            id=new_id(),
            keyword_type="品牌",
            keyword=brand,
            reply_terms=[f"@小助理 {brand}清单"],
        )
        for brand in brands
    ]


def persisted_brand_options(config: RealtimeTalkConfig) -> list[str]:
    return [rule.keyword for rule in config.brand_reply_rules if rule.keyword_type == "品牌" and rule.keyword]


def parsed_brand_options_from_catalog(config: RealtimeTalkConfig) -> list[str]:
    values: list[str] = []
    for brand_values in category_brands().values():
        values.extend(brand_values)
    for rule in config.sale_status_rules:
        values.extend([rule.brand, *rule.aliases])
    cleaned: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and not text.startswith("#") and text not in {"品牌", "在售品牌"} and text not in cleaned:
            cleaned.append(text)
    return sorted(cleaned)


if __name__ == "__main__":
    main()
