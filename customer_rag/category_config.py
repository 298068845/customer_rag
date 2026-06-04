from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import yaml


_GENERIC_QUERY_ALIASES = {"锅"}


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


def category_aliases(path: str | Path = "category_aliases.yaml") -> dict[str, list[str]]:
    config_path = Path(path)
    mtime = config_path.stat().st_mtime if config_path.exists() else None
    global _CACHE_PATH, _CACHE_MTIME, _CACHE_ALIASES
    if _CACHE_ALIASES is not None and _CACHE_PATH == config_path and _CACHE_MTIME == mtime:
        return _CACHE_ALIASES

    aliases = DEFAULT_CATEGORY_ALIASES
    if config_path.exists():
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            aliases = _parse_aliases(payload)
        except (OSError, yaml.YAMLError):
            aliases = DEFAULT_CATEGORY_ALIASES

    _CACHE_PATH = config_path
    _CACHE_MTIME = mtime
    _CACHE_ALIASES = aliases
    return aliases


def save_category_aliases(aliases: dict[str, list[str]], path: str | Path = "category_aliases.yaml") -> None:
    config_path = Path(path)
    payload = {
        "categories": {
            category: {"aliases": _clean_terms(category_aliases)}
            for category, category_aliases in aliases.items()
            if str(category).strip()
        }
    }
    config_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    clear_category_cache()


def add_category_terms(terms: list[str], path: str | Path = "category_aliases.yaml") -> int:
    aliases = {category: list(alias_values) for category, alias_values in category_aliases(path).items()}
    category_by_lower = {category.lower(): category for category in aliases}
    existing = set(category_by_lower)
    for alias_values in aliases.values():
        existing.update(alias.lower() for alias in alias_values)

    added = 0
    changed = False
    for term in _clean_terms(terms):
        key = term.lower()
        compound_aliases = _compound_aliases(term)
        existing_category = category_by_lower.get(key)
        if existing_category:
            if _merge_aliases(aliases, existing_category, compound_aliases, existing, exclude_key=key):
                changed = True
            continue
        if key in existing:
            continue
        term_aliases = []
        for alias in compound_aliases:
            alias_key = alias.lower()
            if alias_key != key and alias_key not in existing:
                term_aliases.append(alias)
                existing.add(alias_key)
        aliases[term] = term_aliases
        category_by_lower[key] = term
        existing.add(key)
        added += 1
        changed = True
    if changed:
        save_category_aliases(aliases, path)
    return added


def clear_category_cache() -> None:
    global _CACHE_PATH, _CACHE_MTIME, _CACHE_ALIASES
    _CACHE_PATH = None
    _CACHE_MTIME = None
    _CACHE_ALIASES = None


def category_terms(query: str) -> list[str]:
    query_lower = query.lower()
    matched: list[str] = []
    for category, aliases in category_aliases().items():
        terms = [category, *aliases, *_compound_aliases(category)]
        if any(_term_matches_query(term, query_lower) for term in terms):
            matched.extend(terms)
    return _clean_terms(matched)


def all_category_terms() -> list[str]:
    terms: list[str] = []
    for category, aliases in category_aliases().items():
        terms.extend([category, *aliases, *_compound_aliases(category)])
    return _clean_terms(terms)


def _parse_aliases(payload: Any) -> dict[str, list[str]]:
    raw_categories = payload.get("categories", {}) if isinstance(payload, dict) else {}
    parsed: dict[str, list[str]] = {}
    if not isinstance(raw_categories, dict):
        return DEFAULT_CATEGORY_ALIASES

    for category, value in raw_categories.items():
        category_text = str(category).strip()
        if not category_text:
            continue
        aliases: list[str] = []
        if isinstance(value, dict):
            raw_aliases = value.get("aliases", [])
        else:
            raw_aliases = value
        if isinstance(raw_aliases, str):
            aliases = [raw_aliases]
        elif isinstance(raw_aliases, list):
            aliases = [str(alias) for alias in raw_aliases]
        parsed[category_text] = _clean_terms(aliases)
    return parsed or DEFAULT_CATEGORY_ALIASES


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
        key = alias.lower()
        if key == exclude_key or key in value_keys or key in existing:
            continue
        values.append(alias)
        value_keys.add(key)
        existing.add(key)
        changed = True
    return changed
