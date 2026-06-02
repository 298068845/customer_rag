from __future__ import annotations

from dataclasses import dataclass, field

from customer_rag.loaders import LoadedDocument


@dataclass(frozen=True)
class Chunk:
    text: str
    source: str
    title: str
    location: str
    chunk_id: str
    image_paths: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def split_documents(
    documents: list[LoadedDocument],
    chunk_size: int,
    chunk_overlap: int,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for doc_index, doc in enumerate(documents):
        for chunk_index, text in enumerate(_split_text(doc.text, chunk_size, chunk_overlap)):
            chunks.append(
                Chunk(
                    text=text,
                    source=doc.source,
                    title=doc.title,
                    location=doc.location,
                    chunk_id=f"{doc_index}-{chunk_index}",
                    image_paths=doc.image_paths or [],
                    tags=doc.tags or [],
                )
            )
    return chunks


def _split_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(normalized) <= chunk_size:
        return [normalized] if normalized else []

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        if end < len(normalized):
            boundary = max(
                normalized.rfind("\n", start, end),
                normalized.rfind("。", start, end),
                normalized.rfind("；", start, end),
            )
            if boundary > start + chunk_size // 2:
                end = boundary + 1
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(end - chunk_overlap, start + 1)
    return chunks
