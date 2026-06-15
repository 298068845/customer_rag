from __future__ import annotations

from pathlib import Path

from customer_rag.config import LlmConfig, RagConfig
from customer_rag.pipeline import STRONG_KEYWORD_MATCH_SCORE, RagPipeline


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
