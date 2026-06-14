from __future__ import annotations

import re
import hashlib
from pathlib import Path

from customer_rag.category_config import category_brands
from customer_rag.category_config import category_terms as configured_category_terms
from customer_rag.order_flow_image import create_order_flow_image
from customer_rag.vector_store import RetrievedChunk


MODEL_CODE_REMOVE_TRANS = str.maketrans("", "", " \t\r\n._-")
FIELD_BRAND = "\u54c1\u724c"
FIELD_CATEGORY = "\u54c1\u7c7b"
FIELD_MODEL = "\u578b\u53f7/\u89c4\u683c"
FIELD_NOTE = "\u9650\u5236\u8bf4\u660e"
FOOTREST_WITH_TERMS = ("\u6709\u811a\u8e0f", "\u5e26\u811a\u8e0f", "\u811a\u8e0f\u6b3e", "\u811a\u8e0f\u7248")
FOOTREST_WITH_QUERY_TERMS = FOOTREST_WITH_TERMS + ("\u811a\u8e0f",)
FOOTREST_WITHOUT_TERMS = ("\u65e0\u811a\u8e0f", "\u4e0d\u5e26\u811a\u8e0f", "\u4e0d\u8981\u811a\u8e0f")


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
    "刀具",
    "菜刀",
    "刀",
    "锅",
    "沙发",
    "餐桌",
    "椅",
    "柜",
    "绷带",
)


def is_product_query(question: str) -> bool:
    if re.search(r"(?=[A-Za-z0-9._-]*[A-Za-z])(?=[A-Za-z0-9._-]*\d)[A-Za-z0-9._-]{3,}", question):
        return True
    if re.search(r"[\u4e00-\u9fff]{2,}[A-Za-z0-9]{2,}|[A-Za-z0-9]{2,}[\u4e00-\u9fff]{2,}", question):
        return True
    if configured_category_terms(question):
        return True
    return any(word in question for word in PRODUCT_QUERY_WORDS) or any(
        word in question for word in ("有什么", "有哪些", "推荐", "清单")
    )


DEFAULT_OUTPUT_FIELDS = ("图片", "下单链接", "其他说明", "领券链接")


def build_structured_product_answer(
    question: str,
    sources: list[RetrievedChunk],
    system_prompt: str | None = None,
    *,
    require_question_match: bool = True,
    max_products: int = 5,
) -> str | None:
    products = []
    seen: set[str] = set()
    seen_series: set[str] = set()
    max_products = max(1, int(max_products or 5))
    for source in sources:
        fields = _extract_fields(source.text)
        product_name = _product_title(fields.get("型号/规格") or source.title)
        if require_question_match and not _matches_product_question(question, product_name, fields):
            continue
        key = _product_identity(product_name, fields)
        if key in seen:
            continue
        series_key = _product_series_identity(source.title, product_name, fields)
        if series_key in seen_series:
            continue
        seen.add(key)
        seen_series.add(series_key)
        products.append((source, fields, product_name))
        if len(products) >= max_products:
            break

    if not products:
        return None

    output_fields = _output_fields_from_prompt(system_prompt)
    hide_missing_fields = _hide_missing_fields_from_prompt(system_prompt)
    product_blocks: list[str] = []
    for source, fields, product_name in products:
        lines = [product_name]
        rendered_fields: set[str] = set()
        for field_name in output_fields:
            value = _field_value(
                field_name,
                fields,
                product_name,
                source=source,
                hide_missing=hide_missing_fields,
            )
            if value is None:
                continue
            lines.append(f"   - {_display_field_name(field_name)}：{value}")
            rendered_fields.add(field_name)
        if len(lines) <= 2:
            for field_name, value in _fallback_fields(fields):
                if field_name in rendered_fields:
                    continue
                lines.append(f"   - {_display_field_name(field_name)}：{value}")
                rendered_fields.add(field_name)
        product_blocks.append("\n".join(lines))
    return "\n---\n".join(product_blocks).strip()


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
            if field == "其他链接":
                field = "领券链接"
            if field and field not in fields:
                fields.append(field)
    new_fields = {"图片", "下单链接", "其他说明", "其他链接", "领券链接"}
    if not fields or not any(field in new_fields for field in fields):
        return DEFAULT_OUTPUT_FIELDS
    return tuple(fields)


def _include_source_numbers_from_prompt(system_prompt: str | None) -> bool:
    return False


def _hide_missing_fields_from_prompt(system_prompt: str | None) -> bool:
    if not system_prompt:
        return False
    normalized = re.sub(r"\s+", "", system_prompt)
    return any(
        marker in normalized
        for marker in (
            "没查询到不显示",
            "未查询到不显示",
            "查不到不显示",
            "没有结果的不显示",
            "查询到有结果的都必须出现",
        )
    )


def _product_identity(product_name: str, fields: dict[str, str]) -> str:
    link = fields.get("商品链接", "")
    order_flow = fields.get("下单流程&权益", "")
    normalized_flow = re.sub(r"\s+", "", order_flow).lower()
    if link and normalized_flow:
        digest = hashlib.sha1(normalized_flow.encode("utf-8")).hexdigest()
        return f"order:{link.strip().lower()}:{digest}"
    brand = fields.get("品牌", "")
    normalized_name = re.sub(r"\s+", "", product_name).lower()
    if normalized_name:
        return f"product:{brand.strip().lower()}:{normalized_name}"
    if link:
        return f"link:{link.strip().lower()}"
    return f"{brand.strip().lower()}:{normalized_name}"


def _product_series_identity(title: str, product_name: str, fields: dict[str, str]) -> str:
    brand = fields.get(FIELD_BRAND, "").strip().lower()
    first_line = str(title or product_name or "").splitlines()[0]
    normalized_name = _series_model_code(f"{first_line}\n{product_name}") or re.sub(r"\s+", "", first_line).lower()
    variant_text = "\n".join([product_name, fields.get(FIELD_MODEL, ""), fields.get(FIELD_NOTE, "")])
    variant = _footrest_text_state(variant_text)
    return f"{brand}:{normalized_name}:{variant}" if variant else f"{brand}:{normalized_name}"


def _product_title(value: str) -> str:
    title = re.sub(r"\s+", " ", value).strip()
    return title[:120] if title else "未命名商品"


def _field_value(
    field_name: str,
    fields: dict[str, str],
    product_name: str,
    *,
    source: RetrievedChunk | None = None,
    hide_missing: bool = False,
) -> str | None:
    def missing() -> str | None:
        return None if hide_missing else "资料中未找到"

    if field_name == "图片":
        return _answer_image_path(source.image_paths if source else []) or _lazy_order_flow_image(fields, source) or missing()
    if field_name == "品牌":
        return fields.get("品牌") or missing()
    if field_name == "型号/规格":
        return fields.get("型号/规格") or product_name or missing()
    if field_name == "下单流程":
        return _compact(_extract_section(fields.get("下单流程&权益"), ("下单流程", "付款流程")), hide_missing=hide_missing)
    if field_name == "权益":
        return _compact(_extract_section(fields.get("下单流程&权益"), ("店铺权益", "权益", "福利")), hide_missing=hide_missing)
    if field_name == "商品链接":
        return fields.get("商品链接") or missing()
    if field_name == "下单链接":
        return fields.get("商品链接") or missing()
    if field_name in {"限制说明", "其他说明"}:
        return fields.get("限制说明") or missing()
    if field_name in {"其他链接", "领券链接"}:
        return fields.get("其他链接") or missing()
    if field_name == "确认收货后":
        return _compact(_extract_after_receipt(fields.get("下单流程&权益")), hide_missing=hide_missing)
    return fields.get(field_name) or missing()


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
    if "商品链接" in key or "礼金短链接" in key or key == "链接":
        return "商品链接"
    if "下单链接" in key:
        return "下单链接"
    if "其他链接" in key or "领券链接" in key:
        return "其他链接"
    if key == "图片" or "图片" in key:
        return "图片"
    if "其他说明" in key or "限制" in key or "说明" in key:
        return "限制说明"
    if "品类" in key or "类别" in key:
        return "品类"
    return None


def _fallback_fields(fields: dict[str, str]) -> list[tuple[str, str]]:
    preferred = ("商品链接", "限制说明", "其他链接", "品牌", "品类", "型号/规格", "下单流程&权益", "权益")
    result: list[tuple[str, str]] = []
    for field_name in preferred:
        value = fields.get(field_name)
        if value:
            if field_name == "下单流程&权益":
                label = "下单流程"
            elif field_name == "其他链接":
                label = "领券链接"
            else:
                label = field_name
            result.append((label, _compact(value, max_chars=260) or value))
    return result


def _display_field_name(field_name: str) -> str:
    return "领券链接" if field_name == "其他链接" else field_name


def _answer_image_path(image_paths: list[str]) -> str | None:
    if not image_paths:
        return None
    for path in image_paths:
        normalized = path.replace("\\", "/")
        if "/_generated/order_flow/" in normalized:
            return path
    return None


def _lazy_order_flow_image(fields: dict[str, str], source: RetrievedChunk | None) -> str | None:
    if source is None or not source.source:
        return None
    row_number = _source_row_number(source.location)
    if row_number < 1:
        return None
    source_path = Path(source.source)
    if not source_path.exists():
        return None
    product_info = fields.get("型号/规格") or fields.get("产品信息") or ""
    order_flow = fields.get("下单流程&权益") or fields.get("下单流程") or ""
    other_note = fields.get("限制说明") or fields.get("其他说明") or ""
    image_path = create_order_flow_image(
        product_info,
        order_flow,
        other_note,
        source_path=source_path,
        row_number=row_number,
        output_root=source_path.parent / "_generated" / "order_flow",
    )
    return image_path


def _source_row_number(location: str) -> int:
    matches = re.findall(r"(?:行|row)\s*(\d+)", str(location), flags=re.IGNORECASE)
    if not matches:
        return 0
    try:
        return int(matches[-1])
    except ValueError:
        return 0


def _matches_product_question(question: str, product_name: str, fields: dict[str, str]) -> bool:
    query = question.strip()
    brand = fields.get(FIELD_BRAND, "")
    target_text = f"{brand}\n{product_name}\n{fields.get(FIELD_MODEL, '')}\n{fields.get(FIELD_CATEGORY, '')}"
    lowered_target = target_text.lower()

    brand_terms = _known_brand_terms(query)
    if brand_terms and not any(term.lower() == brand.lower() for term in brand_terms):
        return False

    footrest_preference = _footrest_preference(query)
    if footrest_preference:
        footrest_state = _footrest_text_state(target_text)
        if footrest_state != footrest_preference:
            return False

    code_terms = _model_code_terms(query)
    if code_terms:
        return any(_model_code_in_text(term, lowered_target) for term in code_terms)

    if brand_terms:
        remainder_terms = _query_without_brands(query, brand_terms)
        if remainder_terms and any(term.lower() in lowered_target for term in remainder_terms):
            return True

    category_terms = _specific_category_terms_for_query(query, _category_terms(query))
    if category_terms:
        category_text = fields.get(FIELD_CATEGORY, "")
        return any(term in product_name or term in fields.get(FIELD_MODEL, "") or term in category_text for term in category_terms)

    keywords = [word.lower() for word in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", query) if len(word) >= 2]
    return not keywords or any(word in lowered_target for word in keywords)


def _model_code_in_text(term: str, text: str) -> bool:
    lowered = term.lower()
    return lowered in text or _compact_model_code(lowered) in _compact_model_code(text)


def _model_code_terms(query: str) -> list[str]:
    terms = re.findall(r"[A-Za-z]{1,8}\d[A-Za-z0-9._-]{4,}", query)
    terms.extend(re.findall(r"(?=[A-Za-z0-9._-]*\d)[A-Za-z0-9._-]{2,}", query))
    compact_query = _compact_ascii_model_code(query)
    if re.search(r"[a-z]", compact_query) and re.search(r"\d", compact_query) and len(compact_query) >= 5:
        terms.append(compact_query)
    return list(dict.fromkeys(term.lower() for term in terms))


def _series_model_code(value: str) -> str:
    candidates = [
        term
        for term in _model_code_terms(value)
        if re.search(r"[a-z]", term) and re.search(r"\d", term)
    ]
    return max(candidates, key=len) if candidates else ""


def _compact_ascii_model_code(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _compact_model_code(value: str) -> str:
    return value.lower().translate(MODEL_CODE_REMOVE_TRANS)


def _footrest_preference(query: str) -> str:
    if any(term in query for term in FOOTREST_WITHOUT_TERMS):
        return "without"
    if any(term in query for term in FOOTREST_WITH_QUERY_TERMS):
        return "with"
    return ""


def _footrest_text_state(text: str) -> str:
    if any(term in text for term in FOOTREST_WITHOUT_TERMS):
        return "without"
    if any(term in text for term in FOOTREST_WITH_TERMS):
        return "with"
    return ""


def _known_brand_terms(query: str) -> list[str]:
    known_brands = ["美的", "东芝", "九阳", "大宇", "飞利浦", "西屋", "宜盾普", "源氏木语", "菠萝斑马", "OOU", "352"]
    for brands in category_brands().values():
        known_brands.extend(brands)
    lowered = query.lower()
    matched = [brand for brand in known_brands if brand in query or brand.lower() in lowered]
    return list(dict.fromkeys(matched))


def _query_without_brands(query: str, brand_terms: list[str]) -> list[str]:
    terms: list[str] = []
    lowered = query.lower()
    for brand in brand_terms:
        remainder = lowered.replace(brand.lower(), "").strip()
        if len(remainder) >= 2:
            terms.append(remainder)
            terms.extend(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9][a-z0-9._-]{2,}", remainder))
    return list(dict.fromkeys(terms))


def _category_terms(query: str) -> list[str]:
    return configured_category_terms(query)


def _specific_category_terms_for_query(query: str, category_terms: list[str]) -> list[str]:
    if "电动" in query:
        electric_terms = [term for term in category_terms if "电动" in term]
        if electric_terms:
            return electric_terms
    normalized_query = query.strip()
    filtered_terms = [term for term in category_terms if len(term) > 1 or term == normalized_query]
    return filtered_terms or category_terms


def _source_number(source: RetrievedChunk, sources: list[RetrievedChunk]) -> int:
    for index, item in enumerate(sources, start=1):
        if item is source:
            return index
    return 1


def _compact(value: str | None, max_chars: int = 180, *, hide_missing: bool = False) -> str | None:
    if not value:
        return None if hide_missing else "资料中未找到"
    compacted = re.sub(r"\s+", " ", value).strip()
    if not compacted:
        return None if hide_missing else "资料中未找到"
    if len(compacted) <= max_chars:
        return compacted
    return compacted[:max_chars] + "..."
