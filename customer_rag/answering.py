from __future__ import annotations

import re

from customer_rag.vector_store import RetrievedChunk


PRODUCT_QUERY_WORDS = (
    "商品",
    "产品",
    "型号",
    "品类",
    "推荐",
    "有什么",
    "有哪些",
    "电饭煲",
    "饭煲",
    "电磁炉",
    "电压力锅",
    "破壁机",
    "电蒸锅",
    "微波炉",
    "水波炉",
    "咖啡机",
    "按摩椅",
    "床头柜",
    "床",
    "沙发",
    "餐桌",
    "椅",
    "柜",
)


def is_product_query(question: str) -> bool:
    return any(word in question for word in PRODUCT_QUERY_WORDS) or any(
        word in question for word in ("有什么", "有哪些", "推荐", "清单")
    )


DEFAULT_OUTPUT_FIELDS = ("品牌", "型号/规格", "下单流程", "权益", "商品链接", "限制说明")


def build_structured_product_answer(
    question: str,
    sources: list[RetrievedChunk],
    system_prompt: str | None = None,
) -> str | None:
    if not is_product_query(question):
        return None

    products = []
    seen: set[str] = set()
    for source in sources:
        fields = _extract_fields(source.text)
        product_name = fields.get("型号/规格") or source.title
        if not _matches_product_question(question, product_name, fields):
            continue
        key = product_name.strip()
        if key in seen:
            continue
        seen.add(key)
        products.append((source, fields, product_name))
        if len(products) >= 5:
            break

    if not products:
        return None

    output_fields = _output_fields_from_prompt(system_prompt)
    lines = ["根据资料，匹配到以下商品：", ""]
    for index, (source, fields, product_name) in enumerate(products, start=1):
        material_no = _source_number(source, sources)
        lines.append(f"{index}. {product_name}（资料{material_no}）")
        for field_name in output_fields:
            lines.append(f"   - {field_name}：{_field_value(field_name, fields, product_name)}")
        lines.append("")
    return "\n".join(lines).strip()


def _extract_fields(text: str) -> dict[str, str]:
    parts = [part.strip() for part in re.split(r"；(?=[^；：:]{1,40}[:：])", text) if part.strip()]
    fields: dict[str, str] = {}
    for part in parts:
        key, sep, value = part.partition(":")
        if not sep:
            key, sep, value = part.partition("：")
        if not sep:
            continue
        normalized_key = _normalize_key(key)
        if normalized_key:
            fields[normalized_key] = value.strip()
    return fields


def _output_fields_from_prompt(system_prompt: str | None) -> tuple[str, ...]:
    if not system_prompt:
        return DEFAULT_OUTPUT_FIELDS
    fields: list[str] = []
    for line in system_prompt.splitlines():
        match = re.match(r"\s*-\s*([^：:]{1,30})[：:]\s*$", line)
        if match:
            field = match.group(1).strip()
            if field and field not in fields:
                fields.append(field)
    return tuple(fields) if fields else DEFAULT_OUTPUT_FIELDS


def _field_value(field_name: str, fields: dict[str, str], product_name: str) -> str:
    if field_name == "品牌":
        return fields.get("品牌") or "资料中未找到"
    if field_name == "型号/规格":
        return fields.get("型号/规格") or product_name or "资料中未找到"
    if field_name == "下单流程":
        return _compact(_extract_section(fields.get("下单流程&权益"), ("下单流程", "付款流程")))
    if field_name == "权益":
        return _compact(_extract_section(fields.get("下单流程&权益"), ("店铺权益", "权益", "福利")))
    if field_name == "商品链接":
        return fields.get("商品链接") or "资料中未找到"
    if field_name in {"限制说明", "其他说明"}:
        return fields.get("限制说明") or "资料中未找到"
    if field_name == "确认收货后":
        return _compact(_extract_after_receipt(fields.get("下单流程&权益")))
    return fields.get(field_name) or "资料中未找到"


def _extract_section(text: str | None, markers: tuple[str, ...]) -> str | None:
    if not text:
        return None
    for marker in markers:
        index = text.find(marker)
        if index >= 0:
            return text[index:]
    return text


def _extract_after_receipt(text: str | None) -> str | None:
    if not text:
        return None
    marker = "确认收货后"
    index = text.find(marker)
    if index < 0:
        return None
    return text[index:]


def _normalize_key(key: str) -> str | None:
    key = key.replace("\n", " ").strip()
    if key == "品牌":
        return "品牌"
    if "产品信息" in key or "型号" in key or "规格" in key:
        return "型号/规格"
    if "下单流程" in key or "付款流程" in key:
        return "下单流程&权益"
    if "权益" in key or "福利" in key:
        return "权益"
    if "商品链接" in key or key == "链接":
        return "商品链接"
    if "其他说明" in key or "限制" in key or "说明" in key:
        return "限制说明"
    if "品类" in key or "类别" in key:
        return "品类"
    return None


def _matches_product_question(question: str, product_name: str, fields: dict[str, str]) -> bool:
    query = question.strip()
    brand = fields.get("品牌", "")
    target_text = f"{brand}\n{product_name}\n{fields.get('型号/规格', '')}\n{fields.get('品类', '')}"

    brand_terms = _known_brand_terms(query)
    if brand_terms and not any(term == brand or term in target_text for term in brand_terms):
        return False

    category_terms = _category_terms(query)
    if category_terms:
        return any(term in product_name or term in fields.get("型号/规格", "") for term in category_terms)

    keywords = [word for word in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", query) if len(word) >= 2]
    return not keywords or any(word in target_text for word in keywords)


def _known_brand_terms(query: str) -> list[str]:
    known_brands = ("美的", "东芝", "九阳", "大宇", "飞利浦", "西屋", "宜盾普", "源氏木语")
    return [brand for brand in known_brands if brand in query]


def _category_terms(query: str) -> list[str]:
    categories = (
        "电饭煲",
        "饭煲",
        "电磁炉",
        "电压力锅",
        "破壁机",
        "电蒸锅",
        "微波炉",
        "水波炉",
        "咖啡机",
        "按摩椅",
        "床头柜",
        "床",
        "沙发",
        "餐桌",
        "椅",
        "柜",
    )
    return [category for category in categories if category in query]


def _source_number(source: RetrievedChunk, sources: list[RetrievedChunk]) -> int:
    for index, item in enumerate(sources, start=1):
        if item is source:
            return index
    return 1


def _compact(value: str | None, max_chars: int = 180) -> str:
    if not value:
        return "资料中未找到"
    compacted = re.sub(r"\s+", " ", value).strip()
    if len(compacted) <= max_chars:
        return compacted
    return compacted[:max_chars] + "..."
