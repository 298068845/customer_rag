from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import yaml


_GENERIC_QUERY_ALIASES = {"锅"}
_CATEGORY_SEMANTIC_GROUPS = (
    ("智能门锁", "智能锁", "门锁", "指纹锁"),
)
_PLATFORM_TERMS = {
    "京东",
    "天猫",
    "淘宝",
    "抖音",
    "拼多多",
    "小红书",
    "苏宁",
    "唯品会",
}


DEFAULT_CATEGORY_ALIASES: dict[str, list[str]] = {
    "电饭煲": ["饭煲"],
    "电磁炉": [],
    "电压力锅": ["压力锅", "锅"],
    "破壁机": [],
    "电蒸锅": ["蒸锅"],
    "微波炉": [],
    "水波炉": [],
    "咖啡机": [],
    "按摩椅": [],
    "床": ["实木床", "软床", "箱体床"],
    "床头柜": [],
    "刀具": ["菜刀", "刀", "切片工具", "钛刀"],
    "沙发": ["布艺沙发", "皮艺沙发", "功能沙发"],
    "餐桌": [],
    "椅": ["餐椅", "椅子"],
    "柜": ["衣柜", "电视柜", "边柜"],
}

_CACHE_PATH: Path | None = None
_CACHE_MTIME: float | None = None
_CACHE_ALIASES: dict[str, list[str]] | None = None
_CACHE_BRANDS: dict[str, list[str]] | None = None


def category_aliases(path: str | Path = "category_aliases.yaml") -> dict[str, list[str]]:
    aliases, _ = category_catalog(path)
    return aliases


def category_brands(path: str | Path = "category_aliases.yaml") -> dict[str, list[str]]:
    _, brands = category_catalog(path)
    return brands


def category_catalog(path: str | Path = "category_aliases.yaml") -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    config_path = Path(path)
    mtime = config_path.stat().st_mtime if config_path.exists() else None
    global _CACHE_PATH, _CACHE_MTIME, _CACHE_ALIASES, _CACHE_BRANDS
    if _CACHE_ALIASES is not None and _CACHE_BRANDS is not None and _CACHE_PATH == config_path and _CACHE_MTIME == mtime:
        return _CACHE_ALIASES, _CACHE_BRANDS

    aliases = DEFAULT_CATEGORY_ALIASES
    brands: dict[str, list[str]] = {category: [] for category in aliases}
    if config_path.exists():
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            aliases, brands = _parse_catalog(payload)
        except (OSError, yaml.YAMLError):
            aliases = DEFAULT_CATEGORY_ALIASES
            brands = {category: [] for category in aliases}

    _CACHE_PATH = config_path
    _CACHE_MTIME = mtime
    _CACHE_ALIASES = aliases
    _CACHE_BRANDS = brands
    return aliases, brands


def save_category_aliases(aliases: dict[str, list[str]], path: str | Path = "category_aliases.yaml") -> None:
    _, brands = category_catalog(path)
    save_category_catalog(aliases, brands, path)


def save_category_catalog(
    aliases: dict[str, list[str]],
    brands: dict[str, list[str]] | None = None,
    path: str | Path = "category_aliases.yaml",
) -> None:
    config_path = Path(path)
    brand_map = brands or {}
    payload = {
        "categories": {
            category: {
                "aliases": _clean_terms(category_aliases),
                "brands": _clean_terms(brand_map.get(category, [])),
            }
            for category, category_aliases in aliases.items()
            if str(category).strip()
        }
    }
    config_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    clear_category_cache()


def add_category_terms(
    terms: list[str],
    path: str | Path = "category_aliases.yaml",
    category_brand_map: dict[str, list[str]] | None = None,
) -> int:
    aliases, brands = category_catalog(path)
    aliases = {category: list(alias_values) for category, alias_values in aliases.items()}
    brands = {category: list(brand_values) for category, brand_values in brands.items()}
    category_by_lower = {category.lower(): category for category in aliases}
    alias_to_category: dict[str, str] = {}
    existing = set(category_by_lower)
    for alias_values in aliases.values():
        for alias in alias_values:
            existing.add(alias.lower())
    for category, alias_values in aliases.items():
        for alias in alias_values:
            alias_to_category.setdefault(alias.lower(), category)

    added = 0
    changed = False
    for term in _clean_terms(terms):
        normalized_category, term_aliases = _normalize_category_term(term, aliases)
        key = normalized_category.lower()
        existing_category = category_by_lower.get(key) or alias_to_category.get(key)
        if existing_category:
            if _merge_aliases(aliases, existing_category, term_aliases, existing, exclude_key=existing_category.lower()):
                changed = True
            continue
        if key in existing:
            continue
        category_aliases = []
        for alias in term_aliases:
            alias_key = alias.lower()
            if alias_key != key and alias_key not in existing:
                category_aliases.append(alias)
                existing.add(alias_key)
                alias_to_category[alias_key] = normalized_category
        aliases[normalized_category] = category_aliases
        brands.setdefault(normalized_category, [])
        category_by_lower[key] = normalized_category
        existing.add(key)
        added += 1
        changed = True
    for category, brand_values in (category_brand_map or {}).items():
        category_text, category_aliases = _normalize_category_term(category, aliases)
        if not category_text:
            continue
        key = category_text.lower()
        existing_category = category_by_lower.get(key) or alias_to_category.get(key) or category_text
        if existing_category not in aliases:
            aliases[existing_category] = []
            category_by_lower[key] = existing_category
            added += 1
            changed = True
        if _merge_aliases(aliases, existing_category, category_aliases, existing, exclude_key=existing_category.lower()):
            changed = True
        current = brands.setdefault(existing_category, [])
        current_keys = {value.lower() for value in current}
        for brand in _clean_terms([_normalize_brand_term(brand) for brand in brand_values]):
            brand_key = brand.lower()
            if brand_key not in current_keys:
                current.append(brand)
                current_keys.add(brand_key)
                changed = True
    if changed:
        save_category_catalog(aliases, brands, path)
    return added


def clear_category_cache() -> None:
    global _CACHE_PATH, _CACHE_MTIME, _CACHE_ALIASES, _CACHE_BRANDS
    _CACHE_PATH = None
    _CACHE_MTIME = None
    _CACHE_ALIASES = None
    _CACHE_BRANDS = None


def category_terms(query: str) -> list[str]:
    query_lower = query.lower()
    matched: list[str] = []
    for group in _CATEGORY_SEMANTIC_GROUPS:
        if any(term.lower() in query_lower for term in group):
            matched.extend(group)
    for category, aliases in category_aliases().items():
        terms = [category, *aliases, *_compound_aliases(category), *semantic_category_terms(category, aliases)]
        if any(_term_matches_query(term, query_lower) for term in terms):
            matched.extend(terms)
    return _clean_terms(matched)


def all_category_terms() -> list[str]:
    terms: list[str] = []
    for category, aliases in category_aliases().items():
        terms.extend([category, *aliases, *_compound_aliases(category)])
    return _clean_terms(terms)


def semantic_category_terms(category: str, aliases: list[str]) -> list[str]:
    configured_terms = {str(term).strip().lower() for term in [category, *aliases] if str(term).strip()}
    for group in _CATEGORY_SEMANTIC_GROUPS:
        if configured_terms.intersection(term.lower() for term in group):
            return list(group)
    return []


def _parse_catalog(payload: Any) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    raw_categories = payload.get("categories", {}) if isinstance(payload, dict) else {}
    parsed: dict[str, list[str]] = {}
    brands: dict[str, list[str]] = {}
    if not isinstance(raw_categories, dict):
        return DEFAULT_CATEGORY_ALIASES, {category: [] for category in DEFAULT_CATEGORY_ALIASES}

    for category, value in raw_categories.items():
        category_text = str(category).strip()
        if not category_text:
            continue
        aliases: list[str] = []
        brand_values: list[str] = []
        if isinstance(value, dict):
            raw_aliases = value.get("aliases", [])
            raw_brands = value.get("brands", value.get("在售品牌", []))
        else:
            raw_aliases = value
            raw_brands = []
        if isinstance(raw_aliases, str):
            aliases = [raw_aliases]
        elif isinstance(raw_aliases, list):
            aliases = [str(alias) for alias in raw_aliases]
        if isinstance(raw_brands, str):
            brand_values = _split_terms(raw_brands)
        elif isinstance(raw_brands, list):
            brand_values = [str(brand) for brand in raw_brands]
        normalized_category, inferred_aliases = _normalize_category_term(category_text, parsed)
        current_aliases = parsed.setdefault(normalized_category, [])
        for alias in _clean_terms([*aliases, *inferred_aliases]):
            if alias in _PLATFORM_TERMS:
                continue
            if alias.lower() != normalized_category.lower() and alias not in current_aliases:
                current_aliases.append(alias)
        current_brands = brands.setdefault(normalized_category, [])
        current_brand_keys = {brand.lower() for brand in current_brands}
        for brand in _clean_terms([_normalize_brand_term(brand) for brand in brand_values]):
            brand_key = brand.lower()
            if brand_key not in current_brand_keys:
                current_brands.append(brand)
                current_brand_keys.add(brand_key)
    if not parsed:
        parsed = DEFAULT_CATEGORY_ALIASES
        brands = {category: [] for category in parsed}
    for category in parsed:
        brands.setdefault(category, [])
    return parsed, brands


def _split_terms(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,，、;；|\n]+", value or "") if part.strip()]


def _clean_terms(terms: list[str]) -> list[str]:
    cleaned: list[str] = []
    for term in terms:
        value = str(term).strip()
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned


def _compound_aliases(term: str) -> list[str]:
    if not re.search(r"[-－–—丨|/／]", term):
        return []
    parts = re.split(r"\s*[-－–—丨|/／]\s*", term)
    return [part for part in _clean_terms(parts) if part != term]


def _normalize_category_term(
    term: str,
    known_aliases: dict[str, list[str]] | None = None,
) -> tuple[str, list[str]]:
    value = str(term or "").strip()
    if not value:
        return "", []
    parts = _compound_aliases(value)
    if not parts:
        return value, []
    known_category = _known_category_from_parts(parts, known_aliases or {})
    category = known_category or parts[0]
    aliases = [value, *(part for part in parts if part not in _PLATFORM_TERMS)]
    return category, [alias for alias in _clean_terms(aliases) if alias.lower() != category.lower()]


def _known_category_from_parts(parts: list[str], known_aliases: dict[str, list[str]]) -> str:
    category_by_lower = {category.lower(): category for category in known_aliases}
    alias_to_category = {
        alias.lower(): category
        for category, aliases in known_aliases.items()
        for alias in aliases
    }
    for part in parts:
        matched = category_by_lower.get(part.lower())
        if matched:
            return matched
    for part in parts:
        matched = alias_to_category.get(part.lower())
        if matched:
            return matched
    for part in parts:
        matched = DEFAULT_CATEGORY_ALIASES.get(part)
        if matched is not None:
            return part
    return ""


def _normalize_brand_term(term: str) -> str:
    value = str(term or "").strip()
    if not value:
        return ""
    parts = [part for part in re.split(r"\s*[-－–—丨|/／·・]\s*", value) if part.strip()]
    if len(parts) <= 1:
        return value
    non_platform_parts = [part for part in parts if part not in _PLATFORM_TERMS]
    if len(non_platform_parts) == 1 and len(non_platform_parts[0]) >= 2:
        return non_platform_parts[0]
    return value


def _term_matches_query(term: str, query_lower: str) -> bool:
    value = str(term).strip().lower()
    if not value:
        return False
    if value in _GENERIC_QUERY_ALIASES:
        return query_lower == value
    return value in query_lower


def _merge_aliases(
    aliases: dict[str, list[str]],
    category: str,
    new_aliases: list[str],
    existing: set[str],
    *,
    exclude_key: str,
) -> bool:
    changed = False
    values = aliases.setdefault(category, [])
    value_keys = {value.lower() for value in values}
    for alias in new_aliases:
        if alias in _PLATFORM_TERMS:
            continue
        key = alias.lower()
        if key == exclude_key or key in value_keys or key in existing:
            continue
        values.append(alias)
        value_keys.add(key)
        existing.add(key)
        changed = True
    return changed
