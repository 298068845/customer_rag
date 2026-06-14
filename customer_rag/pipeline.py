from __future__ import annotations

import json
import os
import pickle
from dataclasses import dataclass
from pathlib import Path
import queue
import re
import threading
import time
from typing import Callable
import uuid

from customer_rag.answering import build_structured_product_answer, is_product_query
from customer_rag.attributes import NumericCondition, attributes_match, attributes_score, parse_numeric_conditions
from customer_rag.category_config import add_category_terms
from customer_rag.category_config import category_aliases
from customer_rag.category_config import category_brands
from customer_rag.category_config import category_terms as configured_category_terms
from customer_rag.corpus import CorpusItem, CorpusStore
from customer_rag.config import RagConfig
from customer_rag.llm import LocalLlm, strip_thinking
from customer_rag.loaders import LoadedDocument, brand_tags_from_text, category_tags_from_text
from customer_rag.loaders import list_supported_files, load_document_file, load_documents
from customer_rag.splitter import split_documents
from customer_rag.tencent_docs import load_subscriptions, subscription_output_path
from customer_rag.vector_store import RetrievedChunk, VectorStore


FUZZY_FALLBACK_SECONDS = 4.0
STRONG_KEYWORD_MATCH_SCORE = 100.0
RAW_PARSE_CACHE_VERSION = "v1"
MODEL_CODE_REMOVE_TRANS = str.maketrans("", "", " \t\r\n._-")
QUICK_SEARCH_CACHE_VERSION = 1
FOOTREST_WITH_TERMS = ("\u6709\u811a\u8e0f", "\u5e26\u811a\u8e0f", "\u811a\u8e0f\u6b3e", "\u811a\u8e0f\u7248")
FOOTREST_WITH_QUERY_TERMS = FOOTREST_WITH_TERMS + ("\u811a\u8e0f",)
FOOTREST_WITHOUT_TERMS = ("\u65e0\u811a\u8e0f", "\u4e0d\u5e26\u811a\u8e0f", "\u4e0d\u8981\u811a\u8e0f")


@dataclass(frozen=True)
class RagResult:
    answer: str
    sources: list[RetrievedChunk]
    warning: str | None = None
    fallback: bool = False


class RagPipeline:
    def __init__(self, config: RagConfig):
        self.config = config
        self.corpus = CorpusStore(config.index_dir / "corpus.jsonl")
        self.store = VectorStore(config.index_dir, config.embedding_model_path, config.embedding_batch_size)
        self.llm = LocalLlm(config.llm_model_path, config.llm)
        self._corpus_cache_mtime: float | None = None
        self._corpus_cache: list[CorpusItem] | None = None
        self._tag_index_cache: dict[str, list[CorpusItem]] = {}
        self._tag_lookup_cache: dict[str, str] = {}
        self._model_code_index_cache: dict[str, list[CorpusItem]] = {}
        self._category_item_index_cache: dict[str, list[CorpusItem]] = {}
        self._keyword_cache: dict[
            tuple[str, int, tuple[str, ...], str, float | None, float | None],
            list[RetrievedChunk],
        ] = {}

    def ingest(self) -> dict[str, int]:
        source_tags = self._source_tags_from_subscriptions()
        documents = load_documents(self.config.raw_data_dir, source_tags=source_tags)
        added_categories = add_category_terms(
            _category_terms_from_documents(documents, source_tags),
            category_brand_map=_category_brand_map_from_documents(documents, source_tags),
        )
        before = len(self.corpus.list_items())
        self.corpus.add_documents(documents)
        stats = self._rebuild_stats(documents=len(documents), before=before)
        stats["added_categories"] = added_categories
        return stats

    def rebuild_corpus_from_raw(
        self,
        *,
        rebuild_index: bool = True,
        force: bool = False,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> dict[str, int | str | None]:
        def emit(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(max(0, min(percent, 100)), message)

        emit(5, "正在扫描原始文件")

        def load_progress(done: int, total: int, path: Path) -> None:
            if total:
                emit(5 + int(done / total * 55), f"正在解析文件 {done}/{total}：{path.name}")

        source_tags = self._source_tags_from_subscriptions()
        if force:
            raw_paths = list_supported_files(self.config.raw_data_dir)
            documents = load_documents(
                self.config.raw_data_dir,
                progress_callback=load_progress,
                source_tags=source_tags,
            )
            reused_files = 0
            parsed_files = len(raw_paths)
            self._write_raw_parse_manifest(
                {
                    _raw_path_key(path): _raw_file_signature(path, _tags_for_raw_path(path, source_tags))
                    for path in raw_paths
                }
            )
        else:
            documents, parsed_files, reused_files = self._load_raw_documents_incremental(
                source_tags=source_tags,
                progress_callback=load_progress,
            )
        emit(62, "正在同步商品类目配置")
        added_categories = add_category_terms(
            _category_terms_from_documents(documents, source_tags),
            category_brand_map=_category_brand_map_from_documents(documents, source_tags),
        )
        emit(65, "正在写入语料库")
        self.corpus.replace_documents(documents)
        index_error = None
        chunks = 0
        if rebuild_index:
            emit(75, "正在重建向量索引")
            try:
                chunks = self.rebuild_index(
                    lambda percent, message: emit(75 + int(percent * 0.25), message)
                )
            except RuntimeError as exc:
                index_error = str(exc)
            emit(100, "解析和索引重建完成")
        else:
            emit(100, "原始文件解析完成，等待重建向量索引")
        return {
            "documents": len(documents),
            "items": len(self.corpus.list_items()),
            "chunks": chunks,
            "index_error": index_error,
            "added_categories": added_categories,
            "parsed_files": parsed_files,
            "reused_files": reused_files,
        }

    def _load_raw_documents_incremental(
        self,
        *,
        source_tags: dict[str, list[str]],
        progress_callback: Callable[[int, int, Path], None] | None = None,
    ) -> tuple[list[LoadedDocument], int, int]:
        paths = list_supported_files(self.config.raw_data_dir)
        manifest = self._read_raw_parse_manifest()
        previous_items = _items_by_source(self.corpus.list_items())
        documents: list[LoadedDocument] = []
        next_manifest: dict[str, dict] = {}
        parsed_files = 0
        reused_files = 0
        total = len(paths)

        for index, path in enumerate(paths, start=1):
            signature = _raw_file_signature(path, _tags_for_raw_path(path, source_tags))
            key = signature["key"]
            previous_signature = manifest.get(key)
            reusable_items = previous_items.get(key, [])
            if previous_signature == signature and reusable_items:
                documents.extend(_documents_from_items(reusable_items))
                reused_files += 1
            else:
                documents.extend(load_document_file(path, tags=signature["tags"]))
                parsed_files += 1
            next_manifest[key] = signature
            if progress_callback:
                progress_callback(index, total, path)

        self._write_raw_parse_manifest(next_manifest)
        return [doc for doc in documents if doc.text.strip()], parsed_files, reused_files

    def _raw_parse_manifest_path(self) -> Path:
        return self.config.index_dir / "raw_parse_manifest.json"

    def _read_raw_parse_manifest(self) -> dict[str, dict]:
        path = self._raw_parse_manifest_path()
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if payload.get("version") != RAW_PARSE_CACHE_VERSION:
            return {}
        files = payload.get("files", {})
        return files if isinstance(files, dict) else {}

    def _write_raw_parse_manifest(self, files: dict[str, dict]) -> None:
        path = self._raw_parse_manifest_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        tmp_path.write_text(
            json.dumps({"version": RAW_PARSE_CACHE_VERSION, "files": files}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, path)

    def ingest_files(self, paths: list[Path], tags: list[str] | None = None) -> dict[str, int]:
        documents = []
        file_tags = _clean_tags(tags or [])
        for path in paths:
            documents.extend(load_document_file(path, tags=file_tags))
        added_categories = add_category_terms(
            _category_terms_from_documents(documents),
            category_brand_map=_category_brand_map_from_documents(documents),
        )
        before = len(self.corpus.list_items())
        self.corpus.add_documents(documents)
        stats = self._rebuild_stats(documents=len(documents), before=before)
        stats["added_categories"] = added_categories
        return stats

    def ingest_files_with_tags(self, path_tags: list[tuple[Path, list[str]]]) -> dict[str, int]:
        documents: list[LoadedDocument] = []
        for path, tags in path_tags:
            file_tags = _clean_tags(tags)
            for doc in load_document_file(path, tags=file_tags):
                documents.append(
                    LoadedDocument(
                        text=doc.text,
                        source=doc.source,
                        title=doc.title,
                        location=doc.location,
                        image_paths=doc.image_paths,
                        tags=doc.tags,
                        attributes=doc.attributes,
                    )
                )
        added_categories = add_category_terms(
            _category_terms_from_documents(documents),
            category_brand_map=_category_brand_map_from_documents(documents),
        )
        before = len(self.corpus.list_items())
        self.corpus.add_documents(documents)
        stats = self._rebuild_stats(documents=len(documents), before=before)
        stats["added_categories"] = added_categories
        return stats

    def replace_files_with_tags(
        self,
        path_tags: list[tuple[Path, list[str]]],
        *,
        rebuild_index: bool = True,
    ) -> dict[str, int | str | None]:
        documents: list[LoadedDocument] = []
        source_paths = {str(path) for path, _ in path_tags}
        for path, tags in path_tags:
            file_tags = _clean_tags(tags)
            for doc in load_document_file(path, tags=file_tags):
                documents.append(
                    LoadedDocument(
                        text=doc.text,
                        source=doc.source,
                        title=doc.title,
                        location=doc.location,
                        image_paths=doc.image_paths,
                        tags=doc.tags,
                        attributes=doc.attributes,
                    )
                )
        added_categories = add_category_terms(
            _category_terms_from_documents(documents),
            category_brand_map=_category_brand_map_from_documents(documents),
        )
        removed, added = self.corpus.replace_sources(source_paths, documents)
        self._update_raw_parse_manifest_for_paths(path_tags)
        if rebuild_index:
            stats = self._rebuild_stats(documents=len(documents), before=len(self.corpus.list_items()) - added)
        else:
            stats = {
                "documents": len(documents),
                "items": added,
                "chunks": 0,
                "index_error": None,
            }
        stats["removed"] = removed
        stats["added_categories"] = added_categories
        return stats

    def _update_raw_parse_manifest_for_paths(self, path_tags: list[tuple[Path, list[str]]]) -> None:
        if not path_tags:
            return
        manifest = self._read_raw_parse_manifest()
        changed = False
        for path, tags in path_tags:
            if not path.exists() or path.suffix.lower() not in {".txt", ".md", ".csv", ".xlsx", ".xls", ".docx", ".pdf"}:
                continue
            signature = _raw_file_signature(path, tags)
            manifest[signature["key"]] = signature
            changed = True
        if changed:
            self._write_raw_parse_manifest(manifest)

    def rebuild_index(self, progress_callback: Callable[[int, str], None] | None = None) -> int:
        def emit(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(max(0, min(percent, 100)), message)

        emit(5, "正在读取语料")
        documents = [
            LoadedDocument(
                text=item.text,
                source=item.source,
                title=item.title,
                location=item.location,
                image_paths=item.image_paths,
                tags=item.tags,
                attributes=item.attributes,
            )
            for item in self.corpus.list_items()
        ]
        emit(25, "正在切分文档片段")
        chunks = split_documents(
            documents,
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
        )
        emit(60, f"正在写入向量索引：共 {len(chunks)} 个片段")
        self.store.build(
            chunks,
            progress_callback=lambda percent, message: emit(60 + int(percent * 0.4), message),
        )
        emit(100, "向量索引重建完成")
        return len(chunks)

    def ask(
        self,
        question: str,
        system_prompt: str | None = None,
        tags: list[str] | None = None,
    ) -> RagResult:
        started_at = time.monotonic()
        selected_tags = _clean_tags(tags or [])
        warning = None
        precise_lookup = _is_precise_lookup(question)
        product_query = is_product_query(question)
        timeout_seconds = self._search_timeout_seconds(question, precise_lookup=precise_lookup, product_query=product_query)
        deadline = started_at + timeout_seconds
        auto_tags = [] if selected_tags or precise_lookup or product_query else self._auto_search_tags(question)
        search_tags = selected_tags or auto_tags
        tag_match = "all" if selected_tags else "any"
        category_query = _is_category_or_tag_query(question, self._tag_lookup_cache)
        product_query = product_query or category_query
        broad_category_query = product_query and not _known_brand_terms(question) and (
            bool(_category_terms(question)) or category_query
        )
        broad_category_brands = _category_brands_for_query(question) if broad_category_query else []
        keyword_top_k = self.config.top_k * (40 if broad_category_query else 10)
        max_answer_products = self.config.top_k

        model_code_sources = (
            self.model_code_search(
                question,
                keyword_top_k,
                tags=search_tags,
                tag_match=tag_match,
            )
            if precise_lookup
            else []
        )
        if precise_lookup and model_code_sources and model_code_sources[0].score >= STRONG_KEYWORD_MATCH_SCORE:
            confirmed_sources = _dedupe_sources_by_product(model_code_sources)
            answer = build_structured_product_answer(
                question,
                confirmed_sources,
                system_prompt=system_prompt,
                require_question_match=True,
                max_products=max_answer_products,
            )
            if answer:
                return RagResult(answer=answer, sources=confirmed_sources[:max_answer_products])
            return self._fuzzy_fallback_result(
                question,
                confirmed_sources,
                system_prompt,
                search_tags,
                None,
                deadline,
            )

        fast_category_sources = (
            self.category_search(
                question,
                keyword_top_k,
                tags=search_tags,
                tag_match=tag_match,
                deadline=deadline,
            )
            if broad_category_query
            else []
        )
        if fast_category_sources and not _needs_full_keyword_fallback(fast_category_sources, self.config.top_k):
            keyword_sources = fast_category_sources
        else:
            keyword_sources = self.keyword_search(
                question,
                keyword_top_k,
                tags=search_tags,
                tag_match=tag_match,
                deadline=deadline,
            )
            keyword_sources = _merge_sources(fast_category_sources, keyword_sources)
        keyword_sources = _merge_sources(model_code_sources, keyword_sources)
        strong_keyword_match = bool(
            keyword_sources and keyword_sources[0].score >= STRONG_KEYWORD_MATCH_SCORE
        )
        if time.monotonic() >= deadline:
            if strong_keyword_match:
                timeout_sources = _dedupe_sources_by_product(keyword_sources)
                if broad_category_query:
                    timeout_sources = _diversify_sources_by_brand(timeout_sources)
                answer = build_structured_product_answer(
                    question,
                    timeout_sources,
                    system_prompt=system_prompt,
                    require_question_match=True,
                    max_products=max_answer_products,
                )
                if answer:
                    return RagResult(answer=answer, sources=timeout_sources[:max_answer_products])
            return self._fuzzy_fallback_result(question, keyword_sources, system_prompt, search_tags, None, deadline)
        if auto_tags and not keyword_sources:
            keyword_sources = _merge_sources(
                keyword_sources,
                self.keyword_search(question, self.config.top_k * 10, deadline=deadline),
            )
            if time.monotonic() >= deadline:
                return self._fuzzy_fallback_result(question, keyword_sources, system_prompt, search_tags, None, deadline)

        precise_product_lookup = precise_lookup or product_query or strong_keyword_match
        if precise_product_lookup and keyword_sources:
            sources = keyword_sources[: self.config.top_k]
        else:
            try:
                search_k = self.config.top_k * 8 if search_tags else self.config.top_k * 4
                vector_results = _run_with_timeout(
                    lambda: self.store.search(question, search_k),
                    max(0.2, timeout_seconds - (time.monotonic() - started_at)),
                )
                if vector_results is None:
                    return self._fuzzy_fallback_result(
                        question,
                        keyword_sources,
                        system_prompt,
                        selected_tags,
                        None,
                        deadline,
                    )
                vector_sources = _filter_by_tags(vector_results, search_tags, match=tag_match)
                if auto_tags and not vector_sources and not keyword_sources:
                    fallback_vector_results = _run_with_timeout(
                        lambda: self.store.search(question, self.config.top_k * 4),
                        max(0.2, timeout_seconds - (time.monotonic() - started_at)),
                    )
                    if fallback_vector_results is None:
                        return self._fuzzy_fallback_result(
                            question,
                            keyword_sources,
                            system_prompt,
                            selected_tags,
                            None,
                            deadline,
                        )
                    vector_sources = _merge_sources(vector_sources, fallback_vector_results)
                sources = _merge_sources(keyword_sources[: self.config.top_k], vector_sources)[: self.config.top_k]
            except (FileNotFoundError, RuntimeError) as exc:
                warning = f"向量索引暂不可用，已切换为关键词检索：{exc}"
                sources = keyword_sources[: self.config.top_k]
        conditions = parse_numeric_conditions(question)
        attribute_sources: list[RetrievedChunk] = []
        if conditions:
            attribute_sources = self.attribute_search(
                question,
                conditions,
                self.config.top_k * 10,
                tags=search_tags,
                tag_match=tag_match,
                deadline=deadline,
            )
            if time.monotonic() >= deadline:
                return self._fuzzy_fallback_result(question, keyword_sources or sources, system_prompt, search_tags, None, deadline)
            if auto_tags and not attribute_sources:
                attribute_sources = self.attribute_search(question, conditions, self.config.top_k * 10, deadline=deadline)
                if time.monotonic() >= deadline:
                    return self._fuzzy_fallback_result(question, keyword_sources or sources, system_prompt, search_tags, None, deadline)
            sources = _merge_sources(attribute_sources, sources)
        sources = _apply_numeric_conditions(
            _filter_weak_sources(_dedupe_sources_by_product(sources)),
            conditions,
        )[: self.config.top_k]

        answer_sources = sources
        if precise_product_lookup and keyword_sources:
            answer_sources = _apply_numeric_conditions(
                _dedupe_sources_by_product(_merge_sources(sources, keyword_sources)),
                conditions,
            )
        if product_query:
            answer_sources = _apply_numeric_conditions(
                _dedupe_sources_by_product(_merge_sources(_merge_sources(attribute_sources, keyword_sources), sources)),
                conditions,
            )
            if broad_category_query:
                answer_sources = _filter_sources_by_known_brands(answer_sources, broad_category_brands)
                answer_sources = _diversify_sources_by_brand(answer_sources)
                sources = answer_sources[:max_answer_products]
        answer = build_structured_product_answer(
            question,
            answer_sources,
            system_prompt=system_prompt,
            require_question_match=not conditions,
            max_products=max_answer_products,
        )
        if answer is None:
            if product_query:
                return self._fuzzy_fallback_result(
                    question,
                    keyword_sources,
                    system_prompt,
                    search_tags,
                    None,
                    deadline,
                )
            else:
                if time.monotonic() - started_at >= timeout_seconds:
                    return self._fuzzy_fallback_result(
                        question,
                        keyword_sources or sources,
                        system_prompt,
                        search_tags,
                        None,
                        deadline,
                    )
                llm_answer = _run_text_with_timeout(
                    lambda: self.llm.answer(question, sources, system_prompt=system_prompt),
                    max(0.2, timeout_seconds - (time.monotonic() - started_at)),
                )
                if llm_answer is None:
                    return self._fuzzy_fallback_result(
                        question,
                        keyword_sources or sources,
                        system_prompt,
                        search_tags,
                        None,
                        deadline,
                    )
                answer = llm_answer
        return RagResult(answer=strip_thinking(answer), sources=sources, warning=warning)

    def _search_timeout_seconds(self, question: str, *, precise_lookup: bool, product_query: bool) -> float:
        if precise_lookup or parse_numeric_conditions(question):
            return max(1.0, float(self.config.precise_search_timeout_seconds))
        if product_query and not _known_brand_terms(question) and _category_terms(question):
            return max(12.0, float(self.config.product_search_timeout_seconds))
        if product_query:
            return max(1.0, float(self.config.product_search_timeout_seconds))
        return max(1.0, float(self.config.search_timeout_seconds))

    def _fuzzy_fallback_result(
        self,
        question: str,
        candidates: list[RetrievedChunk],
        system_prompt: str | None,
        selected_tags: list[str],
        message: str | None,
        deadline: float | None = None,
    ) -> RagResult:
        sources = candidates[: self.config.top_k]
        if not sources and _has_time_left(deadline):
            sources = self.keyword_search(question, self.config.top_k, tags=selected_tags, tag_match="all", deadline=deadline)
        if not sources and _has_time_left(deadline):
            sources = self.keyword_search(question, self.config.top_k, deadline=deadline)
        sources = _dedupe_sources_by_product(sources)[: self.config.top_k]
        answer = build_structured_product_answer(
            question,
            sources,
            system_prompt=system_prompt,
            require_question_match=bool(_known_brand_terms(question)),
            max_products=self.config.top_k,
        )
        if answer is None:
            if _known_brand_terms(question) and not _model_code_queries(question):
                sources = []
                answer = "资料中未找到相关信息。"
            else:
                answer = _format_fuzzy_sources(sources)
        return RagResult(answer=answer, sources=sources, warning=message, fallback=True)

    def model_code_search(
        self,
        question: str,
        top_k: int,
        tags: list[str] | None = None,
        *,
        tag_match: str = "all",
    ) -> list[RetrievedChunk]:
        query_codes = _model_code_queries(question)
        if not query_codes:
            return []
        selected_tags = _clean_tags(tags or [])
        selected_tag_set = set(selected_tags)
        self._corpus_items()

        scored: dict[str, RetrievedChunk] = {}
        for query_code in query_codes:
            for code, items in _model_code_candidates(self._model_code_index_cache, query_code):
                match_score = _model_code_match_score(query_code, code)
                if match_score <= 0:
                    continue
                for item in items:
                    if selected_tags and not _tags_match(item.tags, selected_tag_set, tag_match):
                        continue
                    current = scored.get(item.id)
                    score = match_score + _model_code_field_bonus(query_code, item)
                    if current is not None and current.score >= score:
                        continue
                    scored[item.id] = RetrievedChunk(
                        text=item.text,
                        source=item.source,
                        title=item.title,
                        location=item.location,
                        score=score,
                        image_paths=item.image_paths,
                        tags=item.tags,
                        attributes=item.attributes,
                    )
        results = list(scored.values())
        results.sort(key=lambda source: source.score, reverse=True)
        return results[:top_k]

    def category_search(
        self,
        question: str,
        top_k: int,
        tags: list[str] | None = None,
        *,
        tag_match: str = "all",
        deadline: float | None = None,
    ) -> list[RetrievedChunk]:
        if not _has_time_left(deadline):
            return []
        selected_tags = _clean_tags(tags or [])
        selected_tag_set = set(selected_tags)
        self._corpus_items()
        category_terms = _category_query_terms(question, self._tag_lookup_cache)
        if not category_terms:
            return []

        by_id: dict[str, CorpusItem] = {}
        for term in category_terms:
            for key, items in _category_item_candidates(self._category_item_index_cache, term):
                for item in items:
                    by_id[item.id] = item
        if not by_id:
            return []

        terms = _query_terms(question)
        normalized_question = question.lower().strip()
        brand_terms = _known_brand_terms(normalized_question)
        expanded_category_terms = _category_terms_for_query_expansion(
            normalized_question,
            brand_terms,
            _category_terms(normalized_question) or category_terms,
        )
        code_terms = [term for term in terms if _is_model_code(term)]

        results: list[RetrievedChunk] = []
        for index, item in enumerate(by_id.values()):
            if index % 64 == 0 and not _has_time_left(deadline):
                break
            if selected_tags and not _tags_match(item.tags, selected_tag_set, tag_match):
                continue
            score = _keyword_score(
                normalized_question,
                terms,
                brand_terms=brand_terms,
                category_terms=expanded_category_terms,
                code_terms=code_terms,
                title=item.title,
                location=item.location,
                source=item.source,
                text=item.text,
            )
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
                    attributes=item.attributes,
                )
            )
        results.sort(key=lambda source: source.score, reverse=True)
        return results[:top_k]

    def keyword_search(
        self,
        question: str,
        top_k: int,
        tags: list[str] | None = None,
        *,
        tag_match: str = "all",
        deadline: float | None = None,
    ) -> list[RetrievedChunk]:
        if not _has_time_left(deadline):
            return []
        terms = _query_terms(question)
        selected_tags = _clean_tags(tags or [])
        selected_tag_set = set(selected_tags)
        corpus_items = self._corpus_items_for_tags(selected_tags, match=tag_match)
        cache_key = (
            question,
            top_k,
            tuple(sorted(selected_tag_set)),
            tag_match,
            self._corpus_cache_mtime,
            _category_config_mtime(),
        )
        if cache_key in self._keyword_cache:
            return list(self._keyword_cache[cache_key])

        results: list[RetrievedChunk] = []
        normalized_question = question.lower().strip()
        brand_terms = _known_brand_terms(normalized_question)
        category_terms = _category_terms_for_query_expansion(normalized_question, brand_terms, _category_terms(normalized_question))
        code_terms = [term for term in terms if _is_model_code(term)]
        for index, item in enumerate(corpus_items):
            if index % 64 == 0 and not _has_time_left(deadline):
                break
            if selected_tags and not _tags_match(item.tags, selected_tag_set, tag_match):
                continue
            score = _keyword_score(
                normalized_question,
                terms,
                brand_terms=brand_terms,
                category_terms=category_terms,
                code_terms=code_terms,
                title=item.title,
                location=item.location,
                source=item.source,
                text=item.text,
            )
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
                    attributes=item.attributes,
                )
            )
        results.sort(key=lambda source: source.score, reverse=True)
        sliced = results[:top_k]
        if _has_time_left(deadline):
            self._keyword_cache[cache_key] = list(sliced)
        return sliced

    def attribute_search(
        self,
        question: str,
        conditions: list[NumericCondition],
        top_k: int,
        tags: list[str] | None = None,
        *,
        tag_match: str = "all",
        deadline: float | None = None,
    ) -> list[RetrievedChunk]:
        if not conditions or not _has_time_left(deadline):
            return []

        selected_tags = _clean_tags(tags or [])
        selected_tag_set = set(selected_tags)
        corpus_items = self._corpus_items_for_tags(selected_tags, match=tag_match)
        normalized_question = question.lower().strip()
        terms = _query_terms(question)
        brand_terms = _known_brand_terms(normalized_question)
        category_terms = _category_terms_for_query_expansion(normalized_question, brand_terms, _category_terms(normalized_question))
        code_terms = [term for term in terms if _is_model_code(term)]

        results: list[RetrievedChunk] = []
        for index, item in enumerate(corpus_items):
            if index % 64 == 0 and not _has_time_left(deadline):
                break
            if selected_tags and not _tags_match(item.tags, selected_tag_set, tag_match):
                continue
            searchable_text = "\n".join(
                [
                    item.title.lower(),
                    item.location.lower(),
                    item.source.lower(),
                    item.text.lower(),
                    " ".join(tag.lower() for tag in item.tags),
                ]
            )
            if category_terms and not any(term in searchable_text for term in category_terms):
                continue
            attrs = item.attributes or {}
            if not attributes_match(attrs, conditions):
                continue
            keyword_score = _keyword_score(
                normalized_question,
                terms,
                brand_terms=brand_terms,
                category_terms=category_terms,
                code_terms=code_terms,
                title=item.title,
                location=item.location,
                source=item.source,
                text=item.text,
            )
            score = keyword_score + attributes_score(attrs, conditions) + 30.0
            results.append(
                RetrievedChunk(
                    text=item.text,
                    source=item.source,
                    title=item.title,
                    location=item.location,
                    score=score,
                    image_paths=item.image_paths,
                    tags=item.tags,
                    attributes=attrs,
                )
            )
        results.sort(key=lambda source: source.score, reverse=True)
        return results[:top_k]

    def _corpus_items(self) -> list[CorpusItem]:
        path = self.corpus.path
        mtime = path.stat().st_mtime if path.exists() else None
        if self._corpus_cache is None or self._corpus_cache_mtime != mtime:
            self._corpus_cache = self.corpus.list_items()
            self._corpus_cache_mtime = mtime
            signature = _corpus_file_signature(path) if path.exists() else None
            quick_cache = self._load_quick_search_cache(signature)
            if quick_cache is None:
                self._tag_index_cache = _build_tag_index(self._corpus_cache)
                self._tag_lookup_cache = {tag.lower(): tag for tag in self._tag_index_cache}
                self._model_code_index_cache = _build_model_code_index(self._corpus_cache)
                self._category_item_index_cache = _build_category_item_index(self._corpus_cache)
                self._write_quick_search_cache(signature)
            else:
                (
                    self._tag_index_cache,
                    self._tag_lookup_cache,
                    self._model_code_index_cache,
                    self._category_item_index_cache,
                ) = quick_cache
            self._keyword_cache.clear()
        return self._corpus_cache

    def _quick_search_cache_path(self) -> Path:
        return self.config.index_dir / "quick_search_cache.pkl"

    def _load_quick_search_cache(
        self,
        signature: tuple[int, int] | None,
    ) -> tuple[
        dict[str, list[CorpusItem]],
        dict[str, str],
        dict[str, list[CorpusItem]],
        dict[str, list[CorpusItem]],
    ] | None:
        if signature is None:
            return None
        path = self._quick_search_cache_path()
        if not path.exists():
            return None
        try:
            with path.open("rb") as fp:
                payload = pickle.load(fp)
        except (OSError, pickle.PickleError, EOFError, AttributeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("version") != QUICK_SEARCH_CACHE_VERSION or tuple(payload.get("signature", ())) != signature:
            return None
        tag_index = payload.get("tag_index")
        tag_lookup = payload.get("tag_lookup")
        model_code_index = payload.get("model_code_index")
        category_item_index = payload.get("category_item_index")
        if not all(isinstance(value, dict) for value in (tag_index, tag_lookup, model_code_index, category_item_index)):
            return None
        return tag_index, tag_lookup, model_code_index, category_item_index

    def _write_quick_search_cache(self, signature: tuple[int, int] | None) -> None:
        if signature is None:
            return
        path = self._quick_search_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        payload = {
            "version": QUICK_SEARCH_CACHE_VERSION,
            "signature": signature,
            "tag_index": self._tag_index_cache,
            "tag_lookup": self._tag_lookup_cache,
            "model_code_index": self._model_code_index_cache,
            "category_item_index": self._category_item_index_cache,
        }
        try:
            with tmp_path.open("wb") as fp:
                pickle.dump(payload, fp, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp_path, path)
        except OSError:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    def _corpus_items_for_tags(self, tags: list[str], *, match: str = "all") -> list[CorpusItem]:
        if not tags:
            return self._corpus_items()
        self._corpus_items()
        selected_tags = _clean_tags(tags)
        if not selected_tags:
            return self._corpus_items()
        if match == "any":
            by_id: dict[str, CorpusItem] = {}
            for tag in selected_tags:
                canonical = self._tag_lookup_cache.get(tag.lower(), tag)
                for item in self._tag_index_cache.get(canonical, []):
                    by_id[item.id] = item
            return list(by_id.values())

        selected_set = set(selected_tags)
        smallest_tag = min(
            selected_tags,
            key=lambda tag: len(self._tag_index_cache.get(self._tag_lookup_cache.get(tag.lower(), tag), [])),
        )
        canonical = self._tag_lookup_cache.get(smallest_tag.lower(), smallest_tag)
        candidates = self._tag_index_cache.get(canonical, [])
        return [item for item in candidates if _tags_match(item.tags, selected_set, "all")]

    def _auto_search_tags(self, question: str) -> list[str]:
        terms = _category_tag_candidates(question)
        if not terms:
            return []
        self._corpus_items()
        matched_tags: list[str] = []
        for term in terms:
            tag = self._tag_lookup_cache.get(term.lower())
            if tag and tag not in matched_tags:
                matched_tags.append(tag)
        return matched_tags

    def _source_tags_from_subscriptions(self) -> dict[str, list[str]]:
        subscriptions_path = self.config.index_dir / "tencent_doc_subscriptions.json"
        source_tags: dict[str, list[str]] = {}
        for subscription in load_subscriptions(subscriptions_path):
            tags = _clean_tags(subscription.tags)
            if not tags:
                continue
            output_path = subscription_output_path(subscription, self.config.raw_data_dir)
            for key in (str(output_path), str(output_path.resolve()), output_path.name, output_path.stem):
                source_tags[key] = tags
        return source_tags

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
    ascii_chunks = [
        chunk
        for chunk in re.findall(r"[a-z0-9][a-z0-9._-]{2,}", normalized)
        if len(chunk) >= 4 or any(char.isdigit() for char in chunk)
    ]
    terms.extend(chinese_chunks)
    terms.extend(ascii_chunks)
    model_query = _compact_ascii_model_code(normalized)
    if _is_model_code(model_query) and len(model_query) >= 5:
        terms.append(model_query)
    brand_terms = _known_brand_terms(question)
    terms.extend(brand_terms)
    terms.extend(_query_without_brands(normalized, brand_terms))
    terms.extend(_category_terms_for_query_expansion(question, brand_terms, _category_terms(question)))
    return list(dict.fromkeys(terms))


def _model_code_queries(question: str) -> list[str]:
    queries: list[str] = []
    for term in _query_terms(question):
        compact = _compact_model_code(term)
        if _is_model_code(compact) and len(compact) >= 3:
            queries.append(compact)
    compact_question = _compact_ascii_model_code(question)
    if _is_model_code(compact_question) and len(compact_question) >= 3:
        queries.append(compact_question)
    return list(dict.fromkeys(queries))


def _model_code_candidates(index: dict[str, list[CorpusItem]], query_code: str) -> list[tuple[str, list[CorpusItem]]]:
    exact = index.get(query_code)
    if exact:
        return [(query_code, exact)]

    candidates: list[tuple[str, list[CorpusItem]]] = []
    for code, items in index.items():
        if query_code in code or code in query_code or code.startswith(query_code) or query_code.startswith(code):
            candidates.append((code, items))
    if candidates:
        candidates.sort(key=lambda entry: (abs(len(entry[0]) - len(query_code)), entry[0]))
        return candidates[:80]

    if len(query_code) < 5:
        return []
    fuzzy: list[tuple[int, str, list[CorpusItem]]] = []
    max_distance = 1 if len(query_code) < 8 else 2
    for code, items in index.items():
        if abs(len(code) - len(query_code)) > max_distance:
            continue
        distance = _bounded_levenshtein(query_code, code, max_distance)
        if distance <= max_distance:
            fuzzy.append((distance, code, items))
    fuzzy.sort(key=lambda entry: (entry[0], abs(len(entry[1]) - len(query_code)), entry[1]))
    return [(code, items) for _, code, items in fuzzy[:40]]


def _model_code_match_score(query_code: str, code: str) -> float:
    if query_code == code:
        return 180.0
    if len(query_code) >= 4 and (query_code in code or code in query_code):
        return 125.0
    if len(query_code) >= 4 and (code.startswith(query_code) or query_code.startswith(code)):
        return 110.0
    distance = _bounded_levenshtein(query_code, code, 2)
    if distance == 1:
        return 92.0
    if distance == 2 and len(query_code) >= 8:
        return 78.0
    return 0.0


def _model_code_field_bonus(query_code: str, item: CorpusItem) -> float:
    title = _compact_model_code(item.title)
    location = _compact_model_code(item.location)
    text = _compact_model_code(item.text)
    score = 0.0
    if query_code in title:
        score += 30.0
    if query_code in location:
        score += 18.0
    if query_code in text:
        score += 12.0
    return score


def _build_model_code_index(items: list[CorpusItem]) -> dict[str, list[CorpusItem]]:
    index: dict[str, list[CorpusItem]] = {}
    for item in items:
        text = "\n".join([item.title, item.location, item.source, item.text])
        for code in _extract_model_codes(text):
            bucket = index.setdefault(code, [])
            if not bucket or bucket[-1].id != item.id:
                bucket.append(item)
    return index


def _extract_model_codes(text: str) -> list[str]:
    codes: list[str] = []
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._\-/]{2,}", text):
        compact = _compact_model_code(token)
        if _is_model_code(compact) and len(compact) >= 3:
            codes.append(compact)
    return list(dict.fromkeys(codes))


def _bounded_levenshtein(left: str, right: str, max_distance: int) -> int:
    if left == right:
        return 0
    if abs(len(left) - len(right)) > max_distance:
        return max_distance + 1
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        row_min = current[0]
        for right_index, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            value = min(
                previous[right_index] + 1,
                current[right_index - 1] + 1,
                previous[right_index - 1] + cost,
            )
            current.append(value)
            row_min = min(row_min, value)
        if row_min > max_distance:
            return max_distance + 1
        previous = current
    return previous[-1]


def _is_category_or_tag_query(question: str, tag_lookup: dict[str, str]) -> bool:
    normalized = question.strip().lower()
    if not normalized:
        return False
    if _category_terms(question):
        return True
    return normalized in tag_lookup


def _category_query_terms(question: str, tag_lookup: dict[str, str]) -> list[str]:
    normalized = question.strip().lower()
    terms: list[str] = []
    terms.extend(_category_terms(question))
    terms.extend(_category_tag_candidates(question))
    tag = tag_lookup.get(normalized)
    if tag:
        terms.append(tag.lower())
    if re.fullmatch(r"[\u4e00-\u9fff]{2,}", normalized):
        terms.append(normalized)
    return list(dict.fromkeys(term for term in terms if term))


def _category_item_candidates(index: dict[str, list[CorpusItem]], term: str) -> list[tuple[str, list[CorpusItem]]]:
    normalized = term.strip().lower()
    if not normalized:
        return []
    exact = index.get(normalized)
    if exact:
        return [(normalized, exact)]
    candidates: list[tuple[str, list[CorpusItem]]] = []
    for key, items in index.items():
        if normalized in key or key in normalized:
            candidates.append((key, items))
    candidates.sort(key=lambda entry: (0 if entry[0].startswith(normalized) else 1, len(entry[0]), entry[0]))
    return candidates[:80]


def _build_category_item_index(items: list[CorpusItem]) -> dict[str, list[CorpusItem]]:
    index: dict[str, list[CorpusItem]] = {}
    for item in items:
        terms = [tag.lower() for tag in item.tags if tag]
        terms.extend(_extract_category_fields(item.text))
        for term in dict.fromkeys(term.strip().lower() for term in terms if term.strip()):
            bucket = index.setdefault(term, [])
            if not bucket or bucket[-1].id != item.id:
                bucket.append(item)
    return index


def _extract_category_fields(text: str) -> list[str]:
    values: list[str] = []
    for match in re.finditer(r"(?:^|[；\n])\s*品类\s*[:：]\s*([^；\n]+)", text):
        value = match.group(1).strip()
        if value:
            values.append(value)
            values.extend(_compound_category_aliases(value))
    return values


def _has_time_left(deadline: float | None) -> bool:
    return deadline is None or time.monotonic() < deadline


def _corpus_file_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _run_with_timeout(callable_fn: Callable[[], list[RetrievedChunk]], timeout_seconds: float) -> list[RetrievedChunk] | None:
    return _run_daemon_with_timeout(callable_fn, timeout_seconds)


def _run_text_with_timeout(callable_fn: Callable[[], str], timeout_seconds: float) -> str | None:
    return _run_daemon_with_timeout(callable_fn, timeout_seconds)


def _run_daemon_with_timeout(callable_fn: Callable[[], object], timeout_seconds: float):
    result_queue: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def target() -> None:
        try:
            result_queue.put((True, callable_fn()), block=False)
        except Exception as exc:  # noqa: BLE001 - preserve worker exceptions for caller.
            result_queue.put((False, exc), block=False)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    try:
        ok, value = result_queue.get(timeout=max(0.1, timeout_seconds))
    except queue.Empty:
        return None
    if ok:
        return value
    raise value


def _format_fuzzy_sources(sources: list[RetrievedChunk]) -> str:
    if not sources:
        return "资料中未找到相关信息。"
    lines = ["模糊搜索结果："]
    for index, source in enumerate(sources, start=1):
        preview = re.sub(r"\s+", " ", source.text).strip()
        if len(preview) > 280:
            preview = preview[:280] + "..."
        lines.append(f"{index}. {source.title}")
        lines.append(f"   - 相似度：{source.score:.3f}")
        lines.append(f"   - 来源：{source.location}")
        lines.append(f"   - 摘要：{preview}")
    return "\n".join(lines)


def _keyword_score(
    question: str,
    terms: list[str],
    *,
    brand_terms: list[str],
    category_terms: list[str],
    code_terms: list[str],
    title: str,
    location: str,
    source: str,
    text: str,
) -> float:
    title_text = title.lower()
    location_text = location.lower()
    source_text = source.lower()
    body_text = text.lower()
    file_name = Path(source).name.lower()
    product_field_text = _product_field_text(body_text)
    searchable_text = f"{title_text}\n{location_text}\n{source_text}\n{body_text}"
    source_brand = _extract_search_field(body_text, "品牌")
    score = 0.0
    if question:
        if _search_term_in_text(question, title_text):
            score += 40.0
        if _search_term_in_text(question, product_field_text):
            score += 70.0
        if _search_term_in_text(question, body_text):
            score += 30.0
        if _search_term_in_text(question, file_name):
            score += 18.0
        if _search_term_in_text(question, source_text):
            score += 18.0
        if _search_term_in_text(question, location_text):
            score += 12.0
    for term in terms:
        is_code = _is_model_code(term)
        if _search_term_in_text(term, title_text):
            score += 24.0 if is_code else 8.0
        if _search_term_in_text(term, product_field_text):
            score += 36.0 if is_code else 24.0
        if _search_term_in_text(term, body_text):
            score += 20.0 if is_code else 3.0 + min(body_text.count(term), 5) * 0.3
        if _search_term_in_text(term, file_name):
            score += 10.0 if is_code else 6.0
        if _search_term_in_text(term, source_text):
            score += 14.0 if is_code else 8.0
        if _search_term_in_text(term, location_text):
            score += 6.0 if is_code else 2.0
    if brand_terms:
        matched_brands = [
            term
            for term in brand_terms
            if term == source_brand or (not term.isdigit() and term in searchable_text)
        ]
        if matched_brands:
            score += 24.0
            if any(term == source_brand for term in matched_brands):
                score += 80.0
            if any(term in source_text for term in matched_brands):
                score += 16.0
        else:
            score -= 18.0
    category_terms = _specific_category_terms_for_query(question, category_terms)
    if category_terms:
        matched_categories = [term for term in category_terms if term in searchable_text]
        if matched_categories:
            score += 10.0
        elif brand_terms:
            score -= 6.0
    if brand_terms and category_terms:
        has_brand = any(term in searchable_text for term in brand_terms)
        has_category = any(term in searchable_text for term in category_terms)
        if has_brand and has_category:
            score += 30.0
    if code_terms and all(_search_term_in_text(term, searchable_text) for term in code_terms):
        score += 35.0
    footrest_preference = _footrest_preference(question)
    if footrest_preference:
        footrest_state = _footrest_text_state(searchable_text)
        if footrest_state == footrest_preference:
            score += 90.0
        elif footrest_state:
            score -= 120.0
        else:
            score -= 70.0
    for char in question:
        if "\u4e00" <= char <= "\u9fff" and char in title_text:
            score += 0.08
    if question.isdigit() and _looks_like_sparse_id_mapping(body_text):
        score -= 55.0
    return score


def _is_precise_lookup(question: str) -> bool:
    normalized = question.strip()
    return bool(re.search(r"(?=[A-Za-z0-9._-]*[A-Za-z])(?=[A-Za-z0-9._-]*\d)[A-Za-z0-9._-]{3,}", normalized))


def _is_model_code(value: str) -> bool:
    return bool(re.search(r"[a-z]", value.lower()) and re.search(r"\d", value))


def _product_field_text(text: str) -> str:
    values = []
    for pattern in (
        r"产品信息[^:：]*[:：]\s*([^；]+)",
        r"型号[^:：]*[:：]\s*([^；]+)",
        r"规格[^:：]*[:：]\s*([^；]+)",
    ):
        values.extend(match.group(1) for match in re.finditer(pattern, text))
    return "\n".join(values)


def _specific_category_terms_for_query(query: str, category_terms: list[str]) -> list[str]:
    if "电动" in query:
        electric_terms = [term for term in category_terms if "电动" in term]
        if electric_terms:
            return electric_terms
    normalized_query = query.strip()
    filtered_terms = [term for term in category_terms if len(term) > 1 or term == normalized_query]
    return filtered_terms or category_terms


def _category_terms_for_query_expansion(query: str, brand_terms: list[str], category_terms: list[str]) -> list[str]:
    if brand_terms:
        direct_terms = [term for term in category_terms if term and term in query]
        return direct_terms or []
    return _specific_category_terms_for_query(query, category_terms)


def _extract_search_field(text: str, field: str) -> str:
    match = re.search(rf"(?:^|[；\n])\s*{re.escape(field)}\s*[:：]\s*([^；\n]+)", text)
    return match.group(1).strip().lower() if match else ""


def _search_term_in_text(term: str, text: str) -> bool:
    if term.isdigit():
        return bool(re.search(rf"(?<!\d){re.escape(term)}(?!\d)", text))
    if _is_model_code(term):
        compact_term = _compact_model_code(term)
        if compact_term and compact_term in _compact_model_code(text):
            return True
    return term in text


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


def _looks_like_sparse_id_mapping(text: str) -> bool:
    has_id = bool(re.search(r"商品\s*id|skuid|sku\s*id", text))
    has_link = "短链接" in text or "商品链接" in text or "链接" in text
    has_product = any(marker in text for marker in ("品牌", "品类", "产品信息", "型号", "下单流程", "权益"))
    return has_id and has_link and not has_product


def _known_brand_terms(query: str) -> list[str]:
    known_brands = [
        "美的",
        "东芝",
        "九阳",
        "大宇",
        "飞利浦",
        "西屋",
        "宜盾普",
        "源氏木语",
        "全友",
        "喜临门",
        "金蝉",
        "蓝盒子",
        "雅兰",
        "菠萝斑马",
        "OOU",
        "352",
    ]
    for brands in category_brands().values():
        known_brands.extend(brands)
    lowered = query.lower()
    matched = [brand.lower() for brand in known_brands if brand in query or brand.lower() in lowered]
    return list(dict.fromkeys(matched))


def _query_without_brands(query: str, brand_terms: list[str]) -> list[str]:
    terms: list[str] = []
    for brand in brand_terms:
        remainder = query.replace(brand.lower(), "").strip()
        if len(remainder) >= 2:
            terms.append(remainder)
            terms.extend(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9][a-z0-9._-]{2,}", remainder))
    return terms


def _category_terms(query: str) -> list[str]:
    return [term.lower() for term in configured_category_terms(query)]


def _category_brands_for_query(query: str) -> list[str]:
    matched_terms = set(_category_terms(query))
    if not matched_terms:
        return []
    aliases_by_category = category_aliases()
    brands_by_category = category_brands()
    brands: list[str] = []
    for category, aliases in aliases_by_category.items():
        terms = {category.lower(), *(alias.lower() for alias in aliases)}
        if not terms.intersection(matched_terms):
            continue
        for brand in brands_by_category.get(category, []):
            if brand and brand not in brands:
                brands.append(brand)
    return brands


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


def _dedupe_sources_by_product(sources: list[RetrievedChunk]) -> list[RetrievedChunk]:
    deduped: list[RetrievedChunk] = []
    positions: dict[str, int] = {}
    for source in sources:
        key = _source_product_key(source)
        if key in positions:
            current_index = positions[key]
            current = deduped[current_index]
            if _source_richness(source) > _source_richness(current):
                deduped[current_index] = source
            continue
        positions[key] = len(deduped)
        deduped.append(source)
    return deduped


def _diversify_sources_by_brand(sources: list[RetrievedChunk]) -> list[RetrievedChunk]:
    grouped: dict[str, list[RetrievedChunk]] = {}
    brand_order: list[str] = []
    for source in sources:
        brand = _extract_search_field(source.text.lower(), "品牌") or "__unknown__"
        if brand not in grouped:
            grouped[brand] = []
            brand_order.append(brand)
        grouped[brand].append(source)

    diversified: list[RetrievedChunk] = []
    index = 0
    while len(diversified) < len(sources):
        added = False
        for brand in brand_order:
            brand_sources = grouped[brand]
            if index < len(brand_sources):
                diversified.append(brand_sources[index])
                added = True
        if not added:
            break
        index += 1
    return diversified


def _filter_sources_by_known_brands(sources: list[RetrievedChunk], brands: list[str]) -> list[RetrievedChunk]:
    if not brands:
        return sources
    brand_keys = {brand.lower() for brand in brands if brand}
    matched = [source for source in sources if _source_brand_key(source, brand_keys)]
    return matched or sources


def _source_brand_key(source: RetrievedChunk, brand_keys: set[str]) -> str:
    text = source.text.lower()
    brand = _extract_search_field(text, "品牌")
    if brand in brand_keys:
        return brand
    product = _extract_search_field(text, "产品信息")
    for brand_key in brand_keys:
        if product.startswith(brand_key) or product.startswith(f"【{brand_key}"):
            return brand_key
    return ""


def _filter_weak_sources(sources: list[RetrievedChunk]) -> list[RetrievedChunk]:
    best = max((source.score for source in sources), default=0.0)
    if best < 10:
        return sources
    threshold = max(1.0, best * 0.2)
    return [source for source in sources if source.score >= threshold]


def _apply_numeric_conditions(sources: list[RetrievedChunk], conditions: list) -> list[RetrievedChunk]:
    if not conditions:
        return sources
    matched = [source for source in sources if attributes_match(source.attributes or {}, conditions)]
    if matched:
        return sorted(matched, key=lambda source: (attributes_score(source.attributes or {}, conditions), source.score), reverse=True)
    return sorted(sources, key=lambda source: (attributes_score(source.attributes or {}, conditions), source.score), reverse=True)


def _source_product_key(source: RetrievedChunk) -> str:
    text = source.text
    product_info = _extract_product_key_field(text, ("产品信息", "型号", "规格"))
    brand = _extract_product_key_field(text, ("品牌",))
    if product_info:
        return f"product:{_normalize_product_key_part(brand)}:{_normalize_product_key_part(product_info)}"
    link_match = re.search(r"(?:商品链接|礼金短链接|链接)[:：]\s*([^；\s]+)", text)
    if link_match:
        return "link:" + link_match.group(1).strip().lower()
    return f"chunk:{source.source}:{source.location}:{source.title}"


def _extract_product_key_field(text: str, field_names: tuple[str, ...]) -> str:
    for field_name in field_names:
        match = re.search(rf"(?:^|[；\n])\s*{re.escape(field_name)}[^:：；\n]{{0,20}}[:：]\s*([^；]+)", text)
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return ""


def _normalize_product_key_part(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _source_richness(source: RetrievedChunk) -> int:
    text = source.text
    score = min(int(source.score), 20)
    for marker in ("品牌", "品类", "产品信息", "型号", "下单流程", "权益", "商品链接", "其他说明"):
        if marker in text:
            score += 10
    score += min(len(text) // 120, 10)
    return score


def _clean_tags(tags: list[str]) -> list[str]:
    cleaned = []
    for tag in tags:
        value = str(tag).strip()
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned


def _filter_by_tags(sources: list[RetrievedChunk], tags: list[str], *, match: str = "all") -> list[RetrievedChunk]:
    if not tags:
        return sources
    selected_tags = set(tags)
    return [source for source in sources if _tags_match(source.tags, selected_tags, match)]


def _tags_match(item_tags: list[str], selected_tags: set[str], match: str) -> bool:
    if not selected_tags:
        return True
    item_tag_set = set(item_tags)
    if match == "any":
        return bool(selected_tags.intersection(item_tag_set))
    return selected_tags.issubset(item_tag_set)


def _build_tag_index(items: list[CorpusItem]) -> dict[str, list[CorpusItem]]:
    index: dict[str, list[CorpusItem]] = {}
    for item in items:
        for tag in item.tags:
            value = str(tag).strip()
            if value:
                index.setdefault(value, []).append(item)
    return index


def _items_by_source(items: list[CorpusItem]) -> dict[str, list[CorpusItem]]:
    grouped: dict[str, list[CorpusItem]] = {}
    for item in items:
        grouped.setdefault(_raw_path_key(Path(item.source)), []).append(item)
    return grouped


def _documents_from_items(items: list[CorpusItem]) -> list[LoadedDocument]:
    return [
        LoadedDocument(
            text=item.text,
            source=item.source,
            title=item.title,
            location=item.location,
            image_paths=item.image_paths,
            tags=item.tags,
            attributes=item.attributes,
        )
        for item in items
    ]


def _raw_file_signature(path: Path, tags: list[str]) -> dict:
    stat = path.stat()
    return {
        "key": _raw_path_key(path),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "tags": _clean_tags(tags),
        "parser_version": RAW_PARSE_CACHE_VERSION,
    }


def _tags_for_raw_path(path: Path, source_tags: dict[str, list[str]]) -> list[str]:
    keys = _path_keys_for_raw(path)
    for key, tags in source_tags.items():
        if _normalize_path_value(key) in keys:
            return _clean_tags(tags)
    return []


def _path_keys_for_raw(path: Path) -> set[str]:
    keys = {str(path), str(path.resolve()), path.name, path.stem}
    try:
        keys.add(str(path.resolve().relative_to(Path.cwd().resolve())))
    except ValueError:
        pass
    return {_normalize_path_value(value) for value in keys}


def _raw_path_key(path: Path) -> str:
    return _normalize_path_value(path.resolve())


def _normalize_path_value(value: str | Path) -> str:
    return str(value).replace("\\", "/").lower()


def _category_tag_candidates(question: str) -> list[str]:
    matched: list[str] = []
    query_lower = question.lower()
    for category, aliases in category_aliases().items():
        terms = _clean_tags([category, *aliases, *_compound_category_aliases(category)])
        if any(_category_term_matches_query(term, query_lower) for term in terms):
            matched.extend(terms)
    return _clean_tags(matched)


def _compound_category_aliases(category: str) -> list[str]:
    if not re.search(r"[-－–—丨|/／]", category):
        return []
    return [part for part in _clean_tags(re.split(r"\s*[-－–—丨|/／]\s*", category)) if part != category]


def _category_term_matches_query(term: str, query_lower: str) -> bool:
    value = str(term).strip().lower()
    if not value:
        return False
    if value in {"锅"}:
        return query_lower == value
    return value in query_lower


def _needs_full_keyword_fallback(sources: list[RetrievedChunk], top_k: int) -> bool:
    if len(sources) < min(3, top_k):
        return True
    best_score = max((source.score for source in sources), default=0.0)
    return best_score < 8.0


def _category_config_mtime() -> float | None:
    path = Path("category_aliases.yaml")
    return path.stat().st_mtime if path.exists() else None


def _category_terms_from_documents(
    documents: list[LoadedDocument],
    source_tags: dict[str, list[str]] | None = None,
) -> list[str]:
    initial_tags = {tag for tags in (source_tags or {}).values() for tag in _clean_tags(tags)}
    terms: list[str] = []
    for doc in documents:
        for tag in category_tags_from_text(doc.text):
            if tag not in initial_tags:
                terms.append(tag)
    return _clean_tags(terms)


def _category_brand_map_from_documents(
    documents: list[LoadedDocument],
    source_tags: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    initial_tags = {tag for tags in (source_tags or {}).values() for tag in _clean_tags(tags)}
    category_brands: dict[str, list[str]] = {}
    for doc in documents:
        categories = [tag for tag in category_tags_from_text(doc.text) if tag not in initial_tags]
        brands = brand_tags_from_text(doc.text)
        if not categories or not brands:
            continue
        for category in categories:
            values = category_brands.setdefault(category, [])
            for brand in brands:
                if brand not in values:
                    values.append(brand)
    return category_brands
