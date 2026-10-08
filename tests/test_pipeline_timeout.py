from __future__ import annotations

from pathlib import Path

import pytest

from customer_rag import pipeline as pipeline_module
from customer_rag.config import RagConfig
from customer_rag.corpus import CorpusItem
from customer_rag.pipeline import NO_MATCH_ANSWER, RagPipeline
from customer_rag.vector_store import RetrievedChunk


class Clock:
    now = 0.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def query_pipeline(tmp_path: Path, monkeypatch) -> tuple[RagPipeline, Clock]:
    monkeypatch.chdir(tmp_path)
    clock = Clock()
    monkeypatch.setattr(pipeline_module.time, "monotonic", clock.monotonic)
    return RagPipeline(
        RagConfig(
            raw_data_dir=tmp_path / "raw",
            index_dir=tmp_path / "index",
            embedding_model_path=tmp_path / "embedding",
            llm_model_path=tmp_path / "model.gguf",
        )
    ), clock


def test_missing_product_within_budget_is_not_timeout(query_pipeline, monkeypatch):
    pipeline, _clock = query_pipeline
    monkeypatch.setattr(pipeline.store, "search", lambda *_args: [])

    result = pipeline.ask("美的电饭煲")

    assert result.answer == NO_MATCH_ANSWER
    assert result.sources == []
    assert result.fallback
    assert not result.timed_out


def test_keyword_scan_budget_exhaustion_is_timeout(query_pipeline, monkeypatch):
    pipeline, clock = query_pipeline

    def expired_keyword_search(*_args, deadline, **_kwargs):
        clock.now = deadline
        return []

    monkeypatch.setattr(pipeline, "keyword_search", expired_keyword_search)

    result = pipeline.ask("海信")

    assert result.sources == []
    assert result.fallback
    assert result.timed_out


def test_vector_wait_timeout_is_explicit_even_before_clock_deadline(query_pipeline, monkeypatch):
    pipeline, _clock = query_pipeline
    monkeypatch.setattr(pipeline_module, "_run_with_timeout", lambda *_args: None)

    result = pipeline.ask("海信")

    assert result.fallback
    assert result.timed_out


def test_llm_wait_timeout_is_explicit(query_pipeline, monkeypatch):
    pipeline, _clock = query_pipeline
    monkeypatch.setattr(pipeline.store, "search", lambda *_args: [])
    monkeypatch.setattr(pipeline_module, "_run_text_with_timeout", lambda *_args: None)

    result = pipeline.ask("如何申请售后")

    assert result.fallback
    assert result.timed_out


def test_strong_keyword_result_remains_success_at_deadline(query_pipeline, monkeypatch):
    pipeline, clock = query_pipeline
    source = RetrievedChunk(
        text="品牌: 海信；型号/规格: 电视 X100；商品链接: https://u.jd.com/sample",
        title="电视 X100",
        source="海信.xlsx",
        location="row 1",
        score=180,
    )

    def matched_keyword_search(*_args, deadline, **_kwargs):
        clock.now = deadline
        return [source]

    monkeypatch.setattr(pipeline, "keyword_search", matched_keyword_search)

    result = pipeline.ask("海信")

    assert result.sources == [source]
    assert "电视 X100" in result.answer
    assert not result.fallback
    assert not result.timed_out


def test_missing_model_code_after_cold_load_is_timeout(query_pipeline, monkeypatch):
    pipeline, clock = query_pipeline
    original_load = pipeline._corpus_items

    def slow_load():
        result = original_load()
        clock.now = 9.0
        return result

    monkeypatch.setattr(pipeline, "_corpus_items", slow_load)

    result = pipeline.ask("ABC-999")

    assert result.sources == []
    assert result.fallback
    assert result.timed_out


def test_brand_query_scores_only_brand_candidates_on_cold_start(query_pipeline, monkeypatch):
    pipeline, clock = query_pipeline
    monkeypatch.setattr(pipeline_module, "category_brands", lambda: {"电视": ["海信"]})
    # This brand has no category alias, so tag selection must use brand lookup.
    monkeypatch.setattr(pipeline_module, "_category_tag_candidates", lambda _question: [])
    items = [
        CorpusItem(
            id=str(index),
            title=f"无关产品 {index}",
            text="品牌: 美的；型号/规格: 电饭煲",
            source="美的.xlsx",
            location=f"row {index}",
            created_at="",
            updated_at="",
            tags=["美的"],
        )
        for index in range(130)
    ]
    items.append(
        CorpusItem(
            id="hisense",
            title="海信电视 X100",
            text="品牌: 海信；型号/规格: 电视 X100；商品链接: https://u.jd.com/sample",
            source="海信.xlsx",
            location="row 1",
            created_at="",
            updated_at="",
            tags=["海信"],
        )
    )
    pipeline.corpus.replace_all(items)
    original_score = pipeline_module._keyword_score
    scored_titles = []

    def measured_score(*args, **kwargs):
        scored_titles.append(kwargs["title"])
        clock.now += 0.1
        return original_score(*args, **kwargs)

    monkeypatch.setattr(pipeline_module, "_keyword_score", measured_score)

    result = pipeline.ask("海信")

    assert scored_titles == ["海信电视 X100"]
    assert result.sources[0].title == "海信电视 X100"
    assert not result.fallback
    assert not result.timed_out


def test_brand_cold_loading_keeps_original_deadline(query_pipeline, monkeypatch):
    pipeline, clock = query_pipeline
    monkeypatch.setattr(pipeline_module, "category_brands", lambda: {"电视": ["海信"]})
    monkeypatch.setattr(pipeline_module, "_category_tag_candidates", lambda _question: [])
    original_load = pipeline._corpus_items

    def slow_load():
        result = original_load()
        clock.now = 4.1
        return result

    monkeypatch.setattr(pipeline, "_corpus_items", slow_load)

    result = pipeline.ask("海信")

    assert result.sources == []
    assert result.timed_out


def test_exhausted_worker_budget_does_not_start_work():
    started = []

    result = pipeline_module._run_with_timeout(lambda: started.append(True), 0.0)

    assert result is None
    assert started == []
