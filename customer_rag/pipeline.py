from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from pathlib import Path
import re
import time
from typing import Callable

from customer_rag.answering import build_structured_product_answer, is_product_query
from customer_rag.attributes import NumericCondition, attributes_match, attributes_score, parse_numeric_conditions
from customer_rag.category_config import add_category_terms
from customer_rag.category_config import category_aliases
from customer_rag.category_config import category_terms as configured_category_terms
from customer_rag.corpus import CorpusItem, CorpusStore
from customer_rag.config import RagConfig
from customer_rag.llm import LocalLlm, strip_thinking
from customer_rag.loaders import LoadedDocument, brand_tags_from_text, category_tags_from_text
from customer_rag.loaders import load_document_file, load_documents
from customer_rag.splitter import split_documents
from customer_rag.tencent_docs import load_subscriptions, subscription_output_path
from customer_rag.vector_store import RetrievedChunk, VectorStore


FUZZY_FALLBACK_SECONDS = 3.0


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
        documents = load_documents(
            self.config.raw_data_dir,
            progress_callback=load_progress,
            source_tags=source_tags,
        )
        emit(62, "正在同步商品类目配置")
        added_categories = add_category_terms(
            _category_terms_from_documents(documents, source_tags),
            category_brand_map=_category_brand_map_from_documents(documents, source_tags),
        )
        emit(65, "正在写入语料库")
        self.corpus.clear()
        self.corpus.add_documents(documents)
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
        }

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
        removed = self.corpus.delete_by_sources(source_paths)
        before = len(self.corpus.list_items())
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
        self.corpus.add_documents(documents)
        if rebuild_index:
            stats = self._rebuild_stats(documents=len(documents), before=before)
        else:
            stats = {
                "documents": len(documents),
                "items": len(self.corpus.list_items()) - before,
                "chunks": 0,
                "index_error": None,
            }
        stats["removed"] = removed
        stats["added_categories"] = added_categories
        return stats

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
        auto_tags = [] if selected_tags or precise_lookup else self._auto_search_tags(question)
        search_tags = selected_tags or auto_tags
        tag_match = "all" if selected_tags else "any"

        keyword_sources = self.keyword_search(
            question,
            self.config.top_k * 10,
            tags=search_tags,
            tag_match=tag_match,
        )
        if auto_tags and not keyword_sources:
            keyword_sources = _merge_sources(
                keyword_sources,
                self.keyword_search(question, self.config.top_k * 10),
            )

        precise_product_lookup = precise_lookup or is_product_query(question)
        if precise_product_lookup and keyword_sources:
            sources = keyword_sources[: self.config.top_k]
        else:
            try:
                search_k = self.config.top_k * 8 if search_tags else self.config.top_k * 4
                vector_results = _run_with_timeout(
                    lambda: self.store.search(question, search_k),
                    max(0.2, FUZZY_FALLBACK_SECONDS - (time.monotonic() - started_at)),
                )
                if vector_results is None:
                    return self._fuzzy_fallback_result(
                        question,
                        keyword_sources,
                        system_prompt,
                        selected_tags,
                        None,
                    )
                vector_sources = _filter_by_tags(vector_results, search_tags, match=tag_match)
                if auto_tags and not vector_sources and not keyword_sources:
                    fallback_vector_results = _run_with_timeout(
                        lambda: self.store.search(question, self.config.top_k * 4),
                        max(0.2, FUZZY_FALLBACK_SECONDS - (time.monotonic() - started_at)),
                    )
                    if fallback_vector_results is None:
                        return self._fuzzy_fallback_result(
                            question,
                            keyword_sources,
                            system_prompt,
                            selected_tags,
                            None,
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
            )
            if auto_tags and not attribute_sources:
                attribute_sources = self.attribute_search(question, conditions, self.config.top_k * 10)
            sources = _merge_sources(attribute_sources, sources)
        sources = _apply_numeric_conditions(
            _filter_weak_sources(_dedupe_sources_by_product(sources)),
            conditions,
        )[: self.config.top_k]

        answer_sources = sources
        if is_product_query(question):
            answer_sources = _apply_numeric_conditions(
                _dedupe_sources_by_product(_merge_sources(_merge_sources(attribute_sources, keyword_sources), sources)),
                conditions,
            )
        answer = build_structured_product_answer(
            question,
            answer_sources,
            system_prompt=system_prompt,
            require_question_match=not conditions,
        )
        if answer is None:
            if is_product_query(question):
                return self._fuzzy_fallback_result(
                    question,
                    keyword_sources,
                    system_prompt,
                    search_tags,
                    None,
                )
            else:
                if time.monotonic() - started_at >= FUZZY_FALLBACK_SECONDS:
                    return self._fuzzy_fallback_result(
                        question,
                        keyword_sources or sources,
                        system_prompt,
                        search_tags,
                        None,
                    )
                llm_answer = _run_text_with_timeout(
                    lambda: self.llm.answer(question, sources, system_prompt=system_prompt),
                    max(0.2, FUZZY_FALLBACK_SECONDS - (time.monotonic() - started_at)),
                )
                if llm_answer is None:
                    return self._fuzzy_fallback_result(
                        question,
                        keyword_sources or sources,
                        system_prompt,
                        search_tags,
                        None,
                    )
                answer = llm_answer
        return RagResult(answer=strip_thinking(answer), sources=sources, warning=warning)

    def _fuzzy_fallback_result(
        self,
        question: str,
        candidates: list[RetrievedChunk],
        system_prompt: str | None,
        selected_tags: list[str],
        message: str | None,
    ) -> RagResult:
        sources = candidates[: self.config.top_k]
        if not sources:
            sources = self.keyword_search(question, self.config.top_k, tags=selected_tags, tag_match="all")
        if not sources:
            sources = self.keyword_search(question, self.config.top_k)
        sources = _dedupe_sources_by_product(sources)[: self.config.top_k]
        answer = build_structured_product_answer(
            question,
            sources,
            system_prompt=system_prompt,
            require_question_match=False,
        )
        if answer is None:
            answer = _format_fuzzy_sources(sources)
        return RagResult(answer=answer, sources=sources, warning=message, fallback=True)

    def keyword_search(
        self,
        question: str,
        top_k: int,
        tags: list[str] | None = None,
        *,
        tag_match: str = "all",
    ) -> list[RetrievedChunk]:
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
        category_terms = _category_terms(normalized_question)
        code_terms = [term for term in terms if _is_model_code(term)]
        for item in corpus_items:
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
    ) -> list[RetrievedChunk]:
        if not conditions:
            return []

        selected_tags = _clean_tags(tags or [])
        selected_tag_set = set(selected_tags)
        corpus_items = self._corpus_items_for_tags(selected_tags, match=tag_match)
        normalized_question = question.lower().strip()
        terms = _query_terms(question)
        brand_terms = _known_brand_terms(normalized_question)
        category_terms = _category_terms(normalized_question)
        code_terms = [term for term in terms if _is_model_code(term)]

        results: list[RetrievedChunk] = []
        for item in corpus_items:
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
            self._tag_index_cache = _build_tag_index(self._corpus_cache)
            self._tag_lookup_cache = {tag.lower(): tag for tag in self._tag_index_cache}
            self._keyword_cache.clear()
        return self._corpus_cache

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
    brand_terms = _known_brand_terms(question)
    terms.extend(brand_terms)
    terms.extend(_query_without_brands(normalized, brand_terms))
    terms.extend(_category_terms(question))
    return list(dict.fromkeys(terms))


def _run_with_timeout(callable_fn: Callable[[], list[RetrievedChunk]], timeout_seconds: float) -> list[RetrievedChunk] | None:
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(callable_fn)
    try:
        return future.result(timeout=max(0.1, timeout_seconds))
    except TimeoutError:
        future.cancel()
        return None
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _run_text_with_timeout(callable_fn: Callable[[], str], timeout_seconds: float) -> str | None:
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(callable_fn)
    try:
        return future.result(timeout=max(0.1, timeout_seconds))
    except TimeoutError:
        future.cancel()
        return None
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


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
    score = 0.0
    if question:
        if question in title_text:
            score += 40.0
        if question in product_field_text:
            score += 70.0
        if question in body_text:
            score += 30.0
        if question in file_name:
            score += 18.0
        if question in source_text:
            score += 18.0
        if question in location_text:
            score += 12.0
    for term in terms:
        is_code = _is_model_code(term)
        if term in title_text:
            score += 24.0 if is_code else 8.0
        if term in product_field_text:
            score += 36.0 if is_code else 24.0
        if term in body_text:
            score += 20.0 if is_code else 3.0 + min(body_text.count(term), 5) * 0.3
        if term in file_name:
            score += 10.0 if is_code else 6.0
        if term in source_text:
            score += 14.0 if is_code else 8.0
        if term in location_text:
            score += 6.0 if is_code else 2.0
    if brand_terms:
        matched_brands = [term for term in brand_terms if term in searchable_text]
        if matched_brands:
            score += 24.0
            if any(term in source_text for term in matched_brands):
                score += 16.0
        else:
            score -= 18.0
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
    if code_terms and all(term in searchable_text for term in code_terms):
        score += 35.0
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


def _looks_like_sparse_id_mapping(text: str) -> bool:
    has_id = bool(re.search(r"商品\s*id|skuid|sku\s*id", text))
    has_link = "短链接" in text or "商品链接" in text or "链接" in text
    has_product = any(marker in text for marker in ("品牌", "品类", "产品信息", "型号", "下单流程", "权益"))
    return has_id and has_link and not has_product


def _known_brand_terms(query: str) -> list[str]:
    known_brands = (
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
    )
    lowered = query.lower()
    return [brand.lower() for brand in known_brands if brand in query or brand.lower() in lowered]


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
