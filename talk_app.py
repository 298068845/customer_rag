from __future__ import annotations

import pandas as pd
import streamlit as st

from customer_rag.category_config import category_aliases, category_brands
from customer_rag.talk_rag import (
    BrandSaleStatusRule,
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
    div[data-testid="stButton"] > button {
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
    st.title("话术 RAG 管理台")
    st.caption("实时话术规则、品牌/品类识别和开团日期知识维护。")

    tab_test, tab_realtime = st.tabs(["对话测试", "实时话术"])
    with tab_test:
        render_match_test()
    with tab_realtime:
        render_realtime_talk()


def render_match_test() -> None:
    left, right = st.columns([0.64, 0.36], gap="large")
    with left:
        st.subheader("对话测试")
        question = st.text_input("问题", value="今日清单是什么", placeholder="例如：美的还有卖吗？空气炸锅有吗？源氏木语什么时候开团？")
        result = ENGINE.ask(question or "")
        st.markdown("#### 输出结果")
        st.markdown(f"<div class='talk-card'>{result.answer}</div>", unsafe_allow_html=True)
    with right:
        st.subheader("命中链路")
        for step in result.chain:
            st.markdown(f"<div class='chain-step'>{step}</div>", unsafe_allow_html=True)


def render_realtime_talk() -> None:
    config = STORE.load_realtime_config()
    brand_options = all_brand_options(config)
    category_options = list(category_aliases().keys())

    st.subheader("一、今日清单")
    today_left, today_right = st.columns([0.32, 0.68], gap="large")
    with today_left:
        today_triggers_text = st.text_area("触发词 / 同义问法", value="\n".join(config.today_triggers), height=132)
    with today_right:
        today_template = st.text_area(
            "回复内容",
            value=config.today_template,
            height=80,
            help="支持变量：{date_m_d}、{date_ymd}",
        )
        st.caption("示例：@小助理 {date_m_d}清单")

    st.divider()
    st.subheader("二、品牌清单")
    st.markdown(
        "<div class='section-note'>品牌关键词自动关联商品类目页的“在售品牌”列；品类关键词自动关联“标准类目/同义词”，并回复该行全部在售品牌。</div>",
        unsafe_allow_html=True,
    )
    brand_left, brand_right = st.columns([0.42, 0.58], gap="large")
    with brand_left:
        brand_triggers_text = st.text_area("触发词 / 同义问法", value="\n".join(config.brand_triggers), height=262)
        st.caption("keyword 可命中品牌，也可命中商品类目。")
    with brand_right:
        st.markdown("##### 回复内容规则")
        st.caption("命中品牌：按 `@小助理 {品牌}清单` 回复。")
        st.caption("命中标准类目或同义词：读取商品类目页该行所有“在售品牌”，每个品牌生成一条 `@小助理 {品牌}清单`。")
        st.caption("多条回复用换行分隔，微信插件会按换行后的内容发送。")

    st.divider()
    st.subheader("三、开团日期")
    open_left, open_mid, open_right = st.columns([0.32, 0.33, 0.35], gap="large")
    with open_left:
        open_group_triggers_text = st.text_area("触发词 / 同义问法", value="\n".join(config.open_group_triggers), height=184)
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

    open_group_knowledge = st.text_area("开团日期知识内容", value=config.open_group_knowledge, height=170)

    save_col, hint_col = st.columns([0.16, 0.84], gap="medium")
    if save_col.button("保存实时话术", type="primary", use_container_width=True):
        next_config = RealtimeTalkConfig(
            today_triggers=clean_terms(today_triggers_text),
            today_template=today_template.strip() or "@小助理 {date_m_d}清单",
            brand_triggers=clean_terms(brand_triggers_text),
            brand_reply_rules=[],
            brand_alias_rules=[],
            open_group_triggers=clean_terms(open_group_triggers_text),
            sale_status_rules=rows_to_sale_status_rules(sale_status_rows),
            open_group_knowledge=open_group_knowledge.strip(),
        )
        STORE.save_realtime_config(next_config)
        st.success("实时话术已保存。")
    hint_col.caption("品牌清单和售卖状态会跟随商品类目页的“在售品牌”自动更新。")


def auto_brand_reply_preview_rows(brand_options: list[str], category_options: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for brand in brand_options:
        rows.append({"命中类型": "品牌", "关键词": brand, "回复内容": f"{brand}清单"})
    brands_by_category = category_brands()
    aliases_by_category = category_aliases()
    for category in category_options:
        brands = brands_by_category.get(category, [])
        if not brands:
            continue
        terms = [category, *aliases_by_category.get(category, [])]
        rows.append(
            {
                "命中类型": "品类",
                "关键词": "、".join(terms),
                "回复内容": "\n".join(f"{brand}清单" for brand in brands),
            }
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


def all_brand_options(config: RealtimeTalkConfig) -> list[str]:
    values: list[str] = []
    for brand_values in category_brands().values():
        values.extend(brand_values)
    for rule in config.sale_status_rules:
        values.extend([rule.brand, *rule.aliases])
    cleaned: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return sorted(cleaned)


if __name__ == "__main__":
    main()
