from __future__ import annotations

from pathlib import Path

from customer_rag.config import LlmConfig, RagConfig
from customer_rag.answering import build_structured_product_answer
from customer_rag.pipeline import (
    STRONG_KEYWORD_MATCH_SCORE,
    RagPipeline,
    _dedupe_sources_by_product,
    _known_brand_terms,
    _rank_strong_sources_by_sale_status,
    _rank_sources_for_requested_platform,
)
from customer_rag.vector_store import RetrievedChunk


def make_config(root: Path) -> RagConfig:
    return RagConfig(
        raw_data_dir=root / "raw",
        index_dir=root / "index",
        embedding_model_path=root / "model",
        llm_model_path=root / "model.gguf",
        llm=LlmConfig(),
    )


def test_model_code_search_normalizes_case_and_separators(tmp_path: Path) -> None:
    pipeline = RagPipeline(make_config(tmp_path))
    pipeline.add_corpus(
        title="Product ABC-123",
        text="Brand: Test; Model: ABC-123; Benefit: sample",
    )

    results = pipeline.model_code_search("abc123", top_k=3)

    assert results
    assert results[0].title == "Product ABC-123"
    assert results[0].score >= STRONG_KEYWORD_MATCH_SCORE


def test_model_code_search_allows_small_typo_without_strong_match(tmp_path: Path) -> None:
    pipeline = RagPipeline(make_config(tmp_path))
    pipeline.add_corpus(
        title="Product ABC-123",
        text="Brand: Test; Model: ABC-123; Benefit: sample",
    )

    results = pipeline.model_code_search("abc124", top_k=3)

    assert results
    assert results[0].title == "Product ABC-123"
    assert results[0].score < STRONG_KEYWORD_MATCH_SCORE


def test_standalone_model_code_lookup_returns_fuzzy_match_as_fallback(tmp_path: Path) -> None:
    pipeline = RagPipeline(make_config(tmp_path))
    pipeline.add_corpus(
        title="Product ABC-123",
        text="Brand: Test; Model: ABC-123; Benefit: sample",
    )

    result = pipeline.ask("ABC-124")

    assert result.fallback
    assert result.sources
    assert result.sources[0].title == "Product ABC-123"


def test_platform_specific_one_character_model_typo_is_defaultable(tmp_path: Path) -> None:
    pipeline = RagPipeline(make_config(tmp_path))
    pipeline.add_corpus(
        title="Product ABC-123",
        text="品牌: Test；产品信息: ABC-123；商品链接: https://u.jd.com/example",
    )

    result = pipeline.ask("JD ABC-124")

    assert not result.fallback
    assert result.sources
    assert result.sources[0].title == "Product ABC-123"


def test_product_dedupe_keeps_same_model_on_different_platforms() -> None:
    text_prefix = "\u54c1\u724c: \u6e90\u6c0f\u6728\u8bed\uff1b\u4ea7\u54c1\u4fe1\u606f: \u661f\u671f\u4e94\u00b7\u5806\u53e0\u51f3K36T01041T"
    title = "\u661f\u671f\u4e94\u00b7\u5806\u53e0\u51f3K36T01041T"
    jd = RetrievedChunk(
        text=f"{text_prefix}\uff1b\u5546\u54c1\u94fe\u63a5: https://u.jd.com/VaGGgj9",
        source="jd.xlsx",
        title=title,
        location="row 1",
        score=120,
    )
    taobao = RetrievedChunk(
        text=f"{text_prefix}\uff1b\u5546\u54c1\u94fe\u63a5: https://s.click.taobao.com/BKhOCxk",
        source="taobao.xlsx",
        title=title,
        location="row 2",
        score=120,
    )

    ranked = _rank_sources_for_requested_platform("\u6dd8\u5b9d \u661f\u671f\u4e94\u00b7\u5806\u53e0\u51f3K36T01041T", [jd, taobao])
    deduped = _dedupe_sources_by_product(ranked)

    assert [source.source for source in deduped] == ["taobao.xlsx", "jd.xlsx"]


def test_structured_answer_keeps_same_model_on_different_platforms() -> None:
    text_prefix = "\u54c1\u724c: \u6e90\u6c0f\u6728\u8bed\uff1b\u4ea7\u54c1\u4fe1\u606f: \u661f\u671f\u4e94\u00b7\u5806\u53e0\u51f3K36T01041T"
    title = "\u661f\u671f\u4e94\u00b7\u5806\u53e0\u51f3K36T01041T"
    sources = [
        RetrievedChunk(
            text=f"{text_prefix}\uff1b\u5546\u54c1\u94fe\u63a5: https://u.jd.com/VaGGgj9",
            source="jd.xlsx",
            title=title,
            location="row 1",
            score=120,
        ),
        RetrievedChunk(
            text=f"{text_prefix}\uff1b\u5546\u54c1\u94fe\u63a5: https://s.click.taobao.com/BKhOCxk",
            source="taobao.xlsx",
            title=title,
            location="row 2",
            score=120,
        ),
    ]

    answer = build_structured_product_answer("\u661f\u671f\u4e94\u00b7\u5806\u53e0\u51f3K36T01041T", sources)

    assert answer
    assert "https://u.jd.com/VaGGgj9" in answer
    assert "https://s.click.taobao.com/BKhOCxk" in answer


def test_platform_terms_are_not_treated_as_brands() -> None:
    assert _known_brand_terms("\u4eac\u4e1c\u661f\u671f\u4e94\u00b7\u5806\u53e0\u51f3K36T01041T") == []


def test_platform_ranking_applies_before_category_top_k_cutoff() -> None:
    jd_sources = [
        RetrievedChunk(
            text=f"\u54c1\u724c: \u6e90\u6c0f\u6728\u8bed\uff1b\u4ea7\u54c1\u4fe1\u606f: \u5806\u53e0\u51f3{i}\uff1b\u5546\u54c1\u94fe\u63a5: https://u.jd.com/{i}",
            source=f"jd-{i}.xlsx",
            title=f"jd-{i}",
            location=f"row {i}",
            score=46,
        )
        for i in range(6)
    ]
    taobao = RetrievedChunk(
        text="\u54c1\u724c: \u6e90\u6c0f\u6728\u8bed\uff1b\u4ea7\u54c1\u4fe1\u606f: \u5806\u53e0\u51f3T\uff1b\u5546\u54c1\u94fe\u63a5: https://s.click.taobao.com/BKhOCxk",
        source="taobao.xlsx",
        title="taobao",
        location="row 7",
        score=46,
    )

    ranked = _rank_sources_for_requested_platform("\u6dd8\u5b9d\u5806\u53e0\u51f3", [*jd_sources, taobao])

    assert ranked[0] is taobao


def test_strong_matches_prefer_active_products_only_within_nearby_score_band() -> None:
    sources = [
        RetrievedChunk(text="品牌: A；产品信息: 高分截团；限制说明: 暂时截团", source="1.xlsx", title="高分截团", location="row 1", score=120),
        RetrievedChunk(text="品牌: A；产品信息: 相近在售", source="2.xlsx", title="相近在售", location="row 2", score=115),
        RetrievedChunk(text="品牌: A；产品信息: 低分在售", source="3.xlsx", title="低分在售", location="row 3", score=101),
    ]

    ranked = _rank_strong_sources_by_sale_status(sources)

    assert [source.title for source in ranked] == ["相近在售", "高分截团", "低分在售"]


def test_strong_match_closed_detection_does_not_treat_not_closed_as_closed() -> None:
    closed = RetrievedChunk(text="暂时截团", source="1.xlsx", title="closed", location="row 1", score=120)
    active = RetrievedChunk(text="还没截团", source="2.xlsx", title="active", location="row 2", score=119)

    ranked = _rank_strong_sources_by_sale_status([closed, active])

    assert [source.title for source in ranked] == ["active", "closed"]


def test_confident_fuzzy_matches_also_prefer_active_product() -> None:
    closed = RetrievedChunk(text="暂时截团", source="1.xlsx", title="closed", location="row 1", score=92)
    active = RetrievedChunk(text="在售", source="2.xlsx", title="active", location="row 2", score=92)

    ranked = _rank_strong_sources_by_sale_status([closed, active], minimum_score=92)

    assert [source.title for source in ranked] == ["active", "closed"]
