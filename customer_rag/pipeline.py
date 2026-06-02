from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from customer_rag.answering import build_structured_product_answer, is_product_query
from customer_rag.corpus import CorpusItem, CorpusStore
from customer_rag.config import RagConfig
from customer_rag.llm import LocalLlm, strip_thinking
from customer_rag.loaders import LoadedDocument
from customer_rag.loaders import load_document_file, load_documents
from customer_rag.splitter import split_documents
from customer_rag.vector_store import RetrievedChunk, VectorStore


@dataclass(frozen=True)
class RagResult:
    answer: str
    sources: list[RetrievedChunk]
    warning: str | None = None


class RagPipeline:
    def __init__(self, config: RagConfig):
        self.config = config
        self.corpus = CorpusStore(config.index_dir / "corpus.jsonl")
        self.store = VectorStore(config.index_dir, config.embedding_model_path, config.embedding_batch_size)
        self.llm = LocalLlm(config.llm_model_path, config.llm)

    def ingest(self) -> dict[str, int]:
        documents = load_documents(self.config.raw_data_dir)
        before = len(self.corpus.list_items())
        self.corpus.add_documents(documents)
        return self._rebuild_stats(documents=len(documents), before=before)

    def rebuild_corpus_from_raw(self) -> dict[str, int | str | None]:
        documents = load_documents(self.config.raw_data_dir)
        self.corpus.clear()
        before = 0
        self.corpus.add_documents(documents)
        return self._rebuild_stats(documents=len(documents), before=before)

    def ingest_files(self, paths: list[Path], tags: list[str] | None = None) -> dict[str, int]:
        documents = []
        for path in paths:
            documents.extend(load_document_file(path))
        before = len(self.corpus.list_items())
        self.corpus.add_documents(documents, tags=tags)
        return self._rebuild_stats(documents=len(documents), before=before)

    def ingest_files_with_tags(self, path_tags: list[tuple[Path, list[str]]]) -> dict[str, int]:
        documents: list[LoadedDocument] = []
        for path, tags in path_tags:
            file_tags = _clean_tags(tags)
            for doc in load_document_file(path):
                documents.append(
                    LoadedDocument(
                        text=doc.text,
                        source=doc.source,
                        title=doc.title,
                        location=doc.location,
                        image_paths=doc.image_paths,
                        tags=_clean_tags((doc.tags or []) + file_tags),
                    )
                )
        before = len(self.corpus.list_items())
        self.corpus.add_documents(documents)
        return self._rebuild_stats(documents=len(documents), before=before)

    def rebuild_index(self) -> int:
        documents = [
            LoadedDocument(
                text=item.text,
                source=item.source,
                title=item.title,
                location=item.location,
                image_paths=item.image_paths,
                tags=item.tags,
            )
            for item in self.corpus.list_items()
        ]
        chunks = split_documents(
            documents,
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
        )
        self.store.build(chunks)
        return len(chunks)

    def ask(
        self,
        question: str,
        system_prompt: str | None = None,
        tags: list[str] | None = None,
    ) -> RagResult:
        selected_tags = _clean_tags(tags or [])
        warning = None
        try:
            search_k = self.config.top_k * 8 if selected_tags else self.config.top_k
            sources = _filter_by_tags(self.store.search(question, search_k), selected_tags)[: self.config.top_k]
        except (FileNotFoundError, RuntimeError) as exc:
            warning = f"向量索引暂不可用，已切换为关键词检索：{exc}"
            sources = self.keyword_search(question, self.config.top_k, tags=selected_tags)
        if selected_tags and len(sources) < self.config.top_k:
            sources = _merge_sources(sources, self.keyword_search(question, self.config.top_k, tags=selected_tags))[
                : self.config.top_k
            ]
        answer_sources = sources
        if is_product_query(question):
            answer_sources = _merge_sources(sources, self.keyword_search(question, 50, tags=selected_tags))
        answer = build_structured_product_answer(question, answer_sources, system_prompt=system_prompt)
        if answer is None:
            answer = self.llm.answer(question, sources, system_prompt=system_prompt)
        return RagResult(answer=strip_thinking(answer), sources=sources, warning=warning)

    def keyword_search(self, question: str, top_k: int, tags: list[str] | None = None) -> list[RetrievedChunk]:
        terms = _query_terms(question)
        selected_tags = set(_clean_tags(tags or []))
        results: list[RetrievedChunk] = []
        for item in self.corpus.list_items():
            if selected_tags and not selected_tags.issubset(set(item.tags)):
                continue
            haystack = f"{item.title}\n{item.location}\n{item.text}".lower()
            score = _keyword_score(question.lower(), terms, haystack)
            if score <= 0:
                continue
            results.append(
                RetrievedChunk(
                    text=item.text,
                    source=item.source,
                    title=item.title,
                    location=item.location,
                    score=score,
                    image_paths=item.image_paths,
                    tags=item.tags,
                )
            )
        results.sort(key=lambda source: source.score, reverse=True)
        return results[:top_k]

    def list_corpus(self) -> list[CorpusItem]:
        return self.corpus.list_items()

    def add_corpus(self, title: str, text: str, tags: list[str] | None = None) -> CorpusItem:
        return self.corpus.add(title=title, text=text, tags=tags)

    def update_corpus(
        self,
        item_id: str,
        title: str,
        text: str,
        location: str,
        tags: list[str] | None = None,
    ) -> CorpusItem:
        return self.corpus.update(item_id=item_id, title=title, text=text, location=location, tags=tags)

    def delete_corpus(self, item_id: str) -> bool:
        return self.corpus.delete(item_id)

    def delete_corpus_many(self, item_ids: set[str]) -> int:
        return self.corpus.delete_many(item_ids)

    def add_tags_to_corpus_many(self, item_ids: set[str], tags: list[str]) -> int:
        return self.corpus.add_tags_many(item_ids, tags)

    def deduplicate_corpus(self) -> int:
        return self.corpus.deduplicate()

    def _rebuild_stats(self, documents: int, before: int) -> dict[str, int | str | None]:
        index_error = None
        chunks = 0
        try:
            chunks = self.rebuild_index()
        except RuntimeError as exc:
            index_error = str(exc)
        return {
            "documents": documents,
            "items": len(self.corpus.list_items()) - before,
            "chunks": chunks,
            "index_error": index_error,
        }


def _query_terms(question: str) -> list[str]:
    normalized = question.lower().strip()
    terms = [term for term in re.split(r"[\s,，。；;:：/\\|]+", normalized) if term]
    chinese_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", normalized)
    terms.extend(chinese_chunks)
    return list(dict.fromkeys(terms))


def _keyword_score(question: str, terms: list[str], haystack: str) -> float:
    score = 0.0
    if question and question in haystack:
        score += 10.0
    for term in terms:
        if term in haystack:
            score += 2.0 + min(haystack.count(term), 5) * 0.2
    for char in question:
        if "\u4e00" <= char <= "\u9fff" and char in haystack:
            score += 0.08
    return score


def _merge_sources(primary: list[RetrievedChunk], secondary: list[RetrievedChunk]) -> list[RetrievedChunk]:
    merged: list[RetrievedChunk] = []
    seen: set[tuple[str, str, str]] = set()
    for source in primary + secondary:
        key = (source.source, source.location, source.title)
        if key in seen:
            continue
        seen.add(key)
        merged.append(source)
    return merged


def _clean_tags(tags: list[str]) -> list[str]:
    cleaned = []
    for tag in tags:
        value = str(tag).strip()
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned


def _filter_by_tags(sources: list[RetrievedChunk], tags: list[str]) -> list[RetrievedChunk]:
    if not tags:
        return sources
    selected_tags = set(tags)
    return [source for source in sources if selected_tags.issubset(set(source.tags))]
