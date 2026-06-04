from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NumericCondition:
    field: str
    op: str
    value: float


def extract_attributes(text: str) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    product_text = _field_value(text, "产品信息") or text
    all_text = f"{product_text}\n{text}"

    capacities = _extract_capacities_liter(product_text)
    if capacities:
        attrs["capacity_l"] = capacities[0]
        attrs["capacity_l_values"] = capacities

    dimensions = _extract_dimensions_mm(all_text)
    if dimensions:
        attrs.update(dimensions)

    inches = _extract_values(all_text, r"(\d+(?:\.\d+)?)\s*(?:英寸|寸)")
    if inches:
        attrs["screen_inch"] = inches[0]

    wattages = _extract_values(all_text, r"(\d+(?:\.\d+)?)\s*w\b", flags=re.IGNORECASE)
    if wattages:
        attrs["power_w"] = wattages[0]

    lumens = _extract_values(all_text, r"(\d+(?:\.\d+)?)\s*lm\b", flags=re.IGNORECASE)
    if lumens:
        attrs["lumen_lm"] = lumens[0]

    pit_distances = _extract_values(all_text, r"(\d{3,4})\s*(?:坑距|mm坑距|毫米坑距)")
    if pit_distances:
        attrs["pit_distance_mm"] = pit_distances[0]

    return attrs


def parse_numeric_conditions(question: str) -> list[NumericCondition]:
    normalized = question.lower()
    conditions: list[NumericCondition] = []

    for field, units in (
        ("capacity_l", (r"l", r"升", r"公升")),
        ("screen_inch", (r"英寸", r"寸")),
        ("power_w", (r"w", r"瓦")),
        ("lumen_lm", (r"lm", r"流明")),
        ("pit_distance_mm", (r"坑距",)),
    ):
        condition = _parse_condition_for_unit(normalized, field, units)
        if condition:
            conditions.append(condition)

    has_labeled_dimension = False
    for field, labels in (
        ("width_mm", (r"宽", r"宽度")),
        ("height_mm", (r"高", r"高度")),
        ("depth_mm", (r"深", r"深度", r"厚", r"厚度")),
    ):
        condition = _parse_condition_for_labeled_mm(normalized, field, labels)
        if condition:
            conditions.append(condition)
            has_labeled_dimension = True

    if not has_labeled_dimension:
        condition = _parse_unlabeled_dimension_condition(normalized)
        if condition:
            conditions.append(condition)

    return conditions


def attributes_match(attributes: dict[str, Any], conditions: list[NumericCondition]) -> bool:
    return all(_condition_matches(attributes, condition) for condition in conditions)


def attributes_score(attributes: dict[str, Any], conditions: list[NumericCondition]) -> float:
    score = 0.0
    for condition in conditions:
        if _condition_matches(attributes, condition):
            score += 8.0
        elif condition.field in attributes:
            score -= 12.0
        else:
            score -= 2.0
    return score


def _condition_matches(attributes: dict[str, Any], condition: NumericCondition) -> bool:
    values = _attribute_values(attributes, condition.field)
    if not values:
        return False
    target = condition.value
    if condition.op == "lt":
        return any(value < target for value in values)
    if condition.op == "lte":
        return any(value <= target for value in values)
    if condition.op == "gt":
        return any(value > target for value in values)
    if condition.op == "gte":
        return any(value >= target for value in values)
    return any(value == target for value in values)


def _attribute_values(attributes: dict[str, Any], field: str) -> list[float]:
    raw = attributes.get(field)
    values: list[float] = []
    if isinstance(raw, (int, float)):
        values.append(float(raw))
    elif isinstance(raw, list):
        values.extend(float(value) for value in raw if isinstance(value, (int, float)))
    raw_values = attributes.get(field + "_values")
    if isinstance(raw_values, list):
        values.extend(float(value) for value in raw_values if isinstance(value, (int, float)))
    return values


def _parse_condition_for_unit(
    text: str,
    field: str,
    unit_patterns: tuple[str, ...],
) -> NumericCondition | None:
    unit = r"(?:" + "|".join(unit_patterns) + r")"
    patterns = (
        (rf"(?:小于|低于|少于|不到)\s*(\d+(?:\.\d+)?)\s*{unit}", "lt"),
        (rf"(?:不超过|最多|至多)\s*(\d+(?:\.\d+)?)\s*{unit}", "lte"),
        (rf"(\d+(?:\.\d+)?)\s*{unit}\s*(?:以下|以内|内|之内|及以下|以内的)", "lte"),
        (rf"(?:大于|高于|超过)\s*(\d+(?:\.\d+)?)\s*{unit}", "gt"),
        (rf"(?:不低于|不少于|至少)\s*(\d+(?:\.\d+)?)\s*{unit}", "gte"),
        (rf"(\d+(?:\.\d+)?)\s*{unit}\s*(?:以上|及以上|起)", "gte"),
        (rf"(\d+(?:\.\d+)?)\s*{unit}", "eq"),
    )
    for pattern, op in patterns:
        match = re.search(pattern, text)
        if match:
            return NumericCondition(field=field, op=op, value=float(match.group(1)))
    return None


def _parse_condition_for_labeled_mm(
    text: str,
    field: str,
    labels: tuple[str, ...],
) -> NumericCondition | None:
    label = r"(?:" + "|".join(labels) + r")"
    unit = _DIMENSION_UNIT_PATTERN
    patterns = (
        (rf"{label}\s*(?:小于|低于|少于|不到)\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{unit})?", "lt"),
        (rf"{label}\s*(?:不超过|最多|至多|以内|以下)\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{unit})?", "lte"),
        (rf"{label}\s*(?:大于|高于|超过)\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{unit})?", "gt"),
        (rf"{label}\s*(?:不低于|不少于|至少|以上)\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{unit})?", "gte"),
        (rf"{label}\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{unit})?\s*(?:以下|以内|内|之内|及以下)", "lte"),
        (rf"{label}\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{unit})?\s*(?:以上|及以上|起)", "gte"),
        (rf"{label}\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{unit})?", "eq"),
        (rf"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{unit})\s*{label}", "eq"),
    )
    for pattern, op in patterns:
        match = re.search(pattern, text)
        if match:
            value = _to_mm(float(match.group("value")), match.groupdict().get("unit"))
            return NumericCondition(field=field, op=op, value=value)
    return None


def _parse_unlabeled_dimension_condition(text: str) -> NumericCondition | None:
    pattern = rf"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{_DIMENSION_UNIT_PATTERN})"
    for match in re.finditer(pattern, text):
        if _near_dimension_label(text, match.start(), match.end()):
            continue
        value = _to_mm(float(match.group("value")), match.group("unit"))
        if not 50 <= value <= 5000:
            continue
        field = "width_mm" if _unlabeled_dimension_defaults_to_width(text) else "dimension_mm_values"
        return NumericCondition(field=field, op="eq", value=value)
    return None


def _field_value(text: str, field_name: str) -> str | None:
    match = re.search(rf"(?:^|[；;\n])\s*{re.escape(field_name)}[^:：；\n]*[:：]\s*([^；]+)", text)
    return match.group(1).strip() if match else None


def _extract_capacities_liter(text: str) -> list[float]:
    return [
        value
        for value in _extract_values(text, r"(\d+(?:\.\d+)?)\s*(?:l|L|升|公升)(?!m)")
        if 20 <= value <= 2000
    ]


def _extract_dimensions_mm(text: str) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    width = _extract_dimension_values(text, r"(?:宽|宽度)")
    depth = _extract_dimension_values(text, r"(?:深|深度|厚|厚度)")
    height = _extract_dimension_values(text, r"(?:高|高度)")
    if width:
        attrs["width_mm"] = width[0]
    if depth:
        attrs["depth_mm"] = depth[0]
    if height:
        attrs["height_mm"] = height[0]

    dimension_values: list[float] = []
    for values in (width, depth, height):
        dimension_values.extend(values)

    triple = re.search(
        rf"(\d{{1,5}}(?:\.\d+)?)\s*[x×*]\s*(\d{{1,5}}(?:\.\d+)?)\s*[x×*]\s*(\d{{1,5}}(?:\.\d+)?)\s*({_DIMENSION_UNIT_PATTERN})?",
        text,
        re.IGNORECASE,
    )
    if triple:
        unit = triple.group(4)
        values = [_to_mm(float(triple.group(index)), unit) for index in (1, 2, 3)]
        attrs["width_mm"] = values[0]
        attrs["depth_mm"] = values[1]
        attrs["height_mm"] = values[2]
        dimension_values.extend(values)

    if dimension_values:
        attrs["dimension_mm_values"] = _unique_numbers(dimension_values)

    return attrs


_DIMENSION_UNIT_PATTERN = r"(?:mm|毫米|cm|厘米|公分|m|米)"


def _to_mm(value: float, unit: str | None) -> float:
    normalized = (unit or "mm").lower()
    if normalized in {"cm", "厘米", "公分"}:
        return value * 10
    if normalized in {"m", "米"}:
        return value * 1000
    return value


def _extract_dimension_values(text: str, label_pattern: str) -> list[float]:
    pattern = rf"{label_pattern}\s*[x×*：: ]*\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{_DIMENSION_UNIT_PATTERN})?"
    values: list[float] = []
    for match in re.finditer(pattern, text, re.IGNORECASE):
        value = _to_mm(float(match.group("value")), match.groupdict().get("unit"))
        if 10 <= value <= 10000:
            values.append(value)
    return values


def _near_dimension_label(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 4) : start]
    after = text[end : min(len(text), end + 4)]
    return bool(re.search(r"(?:宽|宽度|高|高度|深|深度|厚|厚度)$", before) or re.search(r"^(?:宽|宽度|高|高度|深|深度|厚|厚度)", after))


def _unlabeled_dimension_defaults_to_width(text: str) -> bool:
    return bool(re.search(r"冰箱|冰柜|冷柜|酒柜|床|床垫|桌|柜|门|窗", text))


def _unique_numbers(values: list[float]) -> list[float]:
    unique: list[float] = []
    seen: set[float] = set()
    for value in values:
        key = round(value, 3)
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def _extract_values(text: str, pattern: str, flags: int = 0) -> list[float]:
    values: list[float] = []
    for match in re.finditer(pattern, text, flags):
        try:
            values.append(float(match.group(1)))
        except ValueError:
            continue
    return values
