from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from customer_rag.attributes import extract_attributes
from customer_rag.loaders import LoadedDocument


@dataclass(frozen=True)
class CorpusItem:
    id: str
    title: str
    text: str
    source: str
    location: str
    created_at: str
    updated_at: str
    image_paths: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)


class CorpusStore:
    def __init__(self, path: Path):
        self.path = path

    def list_items(self) -> list[CorpusItem]:
        if not self.path.exists():
            return []
        items: list[CorpusItem] = []
        with self.path.open("r", encoding="utf-8") as fp:
            for line in fp:
                if line.strip():
                    items.append(_item_from_payload(json.loads(line)))
        return items

    def add(
        self,
        title: str,
        text: str,
        source: str = "manual",
        location: str | None = None,
        tags: list[str] | None = None,
    ) -> CorpusItem:
        now = _now()
        item = CorpusItem(
            id=str(uuid.uuid4()),
            title=title.strip() or "未命名语料",
            text=text.strip(),
            source=source,
            location=location or title.strip() or "手动录入",
            created_at=now,
            updated_at=now,
            tags=_clean_tags(tags or []),
            attributes={},
        )
        if not item.text:
            raise ValueError("语料内容不能为空。")
        items = self.list_items()
        items.append(item)
        self.replace_all(items)
        return item

    def add_documents(self, documents: list[LoadedDocument], tags: list[str] | None = None) -> int:
        items = self.list_items()
        existing_keys = {_dedupe_key(item.source, item.location, item.text) for item in items}
        now = _now()
        for doc in documents:
            if not doc.text.strip():
                continue
            key = _dedupe_key(doc.source, doc.location, doc.text)
            if key in existing_keys:
                continue
            existing_keys.add(key)
            items.append(
                CorpusItem(
                    id=str(uuid.uuid4()),
                    title=doc.title,
                    text=doc.text.strip(),
                    source=doc.source,
                    location=doc.location,
                    created_at=now,
                    updated_at=now,
                    image_paths=doc.image_paths or [],
                    tags=_clean_tags((doc.tags or []) + (tags or [])),
                    attributes=doc.attributes or {},
                )
            )
        self.replace_all(items)
        return len(items)

    def deduplicate(self) -> int:
        items = self.list_items()
        seen: set[tuple[str, str, str]] = set()
        unique_items: list[CorpusItem] = []
        for item in items:
            key = _dedupe_key(item.source, item.location, item.text)
            if key in seen:
                continue
            seen.add(key)
            unique_items.append(item)
        self.replace_all(unique_items)
        return len(items) - len(unique_items)

    def update(self, item_id: str, title: str, text: str, location: str, tags: list[str] | None = None) -> CorpusItem:
        if not text.strip():
            raise ValueError("语料内容不能为空。")
        items = self.list_items()
        updated: CorpusItem | None = None
        next_items: list[CorpusItem] = []
        for item in items:
            if item.id == item_id:
                updated = CorpusItem(
                    id=item.id,
                    title=title.strip() or item.title,
                    text=text.strip(),
                    source=item.source,
                    location=location.strip() or item.location,
                    created_at=item.created_at,
                    updated_at=_now(),
                    image_paths=item.image_paths,
                    tags=_clean_tags(tags if tags is not None else item.tags),
                    attributes=item.attributes,
                )
                next_items.append(updated)
            else:
                next_items.append(item)
        if updated is None:
            raise KeyError(f"未找到语料：{item_id}")
        self.replace_all(next_items)
        return updated

    def delete(self, item_id: str) -> bool:
        items = self.list_items()
        next_items = [item for item in items if item.id != item_id]
        self.replace_all(next_items)
        return len(next_items) != len(items)

    def delete_many(self, item_ids: set[str]) -> int:
        if not item_ids:
            return 0
        items = self.list_items()
        next_items = [item for item in items if item.id not in item_ids]
        self.replace_all(next_items)
        return len(items) - len(next_items)

    def delete_by_sources(self, sources: set[str]) -> int:
        if not sources:
            return 0
        normalized_sources = {_normalize_source(source) for source in sources}
        items = self.list_items()
        next_items = [item for item in items if _normalize_source(item.source) not in normalized_sources]
        self.replace_all(next_items)
        return len(items) - len(next_items)

    def add_tags_many(self, item_ids: set[str], tags: list[str]) -> int:
        clean_tags = _clean_tags(tags)
        if not item_ids or not clean_tags:
            return 0
        changed = 0
        next_items: list[CorpusItem] = []
        for item in self.list_items():
            if item.id in item_ids:
                merged_tags = _clean_tags(item.tags + clean_tags)
                if merged_tags != item.tags:
                    changed += 1
                next_items.append(
                    CorpusItem(
                        id=item.id,
                        title=item.title,
                        text=item.text,
                        source=item.source,
                        location=item.location,
                        created_at=item.created_at,
                        updated_at=_now(),
                        image_paths=item.image_paths,
                        tags=merged_tags,
                        attributes=item.attributes,
                    )
                )
            else:
                next_items.append(item)
        self.replace_all(next_items)
        return changed

    def clear(self) -> None:
        self.replace_all([])

    def replace_all(self, items: list[CorpusItem]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.tmp")
        with tmp_path.open("w", encoding="utf-8") as fp:
            for item in items:
                fp.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
        os.replace(tmp_path, self.path)

    def replace_sources(self, sources: set[str], documents: list[LoadedDocument]) -> tuple[int, int]:
        normalized_sources = {_normalize_source(source) for source in sources}
        current = self.list_items()
        retained = [item for item in current if _normalize_source(item.source) not in normalized_sources]
        removed = len(current) - len(retained)
        existing_keys = {_dedupe_key(item.source, item.location, item.text) for item in retained}
        now = _now()
        added = 0
        for doc in documents:
            if not doc.text.strip():
                continue
            key = _dedupe_key(doc.source, doc.location, doc.text)
            if key in existing_keys:
                continue
            existing_keys.add(key)
            retained.append(
                CorpusItem(
                    id=str(uuid.uuid4()),
                    title=doc.title,
                    text=doc.text.strip(),
                    source=doc.source,
                    location=doc.location,
                    created_at=now,
                    updated_at=now,
                    image_paths=doc.image_paths or [],
                    tags=_clean_tags(doc.tags or []),
                    attributes=doc.attributes or {},
                )
            )
            added += 1
        self.replace_all(retained)
        return removed, added

    def replace_documents(self, documents: list[LoadedDocument]) -> int:
        current_sources = {item.source for item in self.list_items()}
        _, added = self.replace_sources(current_sources, documents)
        return added


def _item_from_payload(payload: dict) -> CorpusItem:
    payload.setdefault("image_paths", [])
    payload.setdefault("tags", [])
    payload.setdefault("attributes", {})
    return CorpusItem(**payload)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dedupe_key(source: str, location: str, text: str) -> tuple[str, str, str]:
    return (_normalize_source(source), location, text.strip())


def _normalize_source(source: str) -> str:
    value = str(source).strip()
    if not value:
        return ""
    try:
        return str(Path(value).resolve()).lower()
    except OSError:
        return value.replace("/", "\\").lower()


def _clean_tags(tags: list[str]) -> list[str]:
    cleaned = []
    for tag in tags:
        value = str(tag).strip()
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned
