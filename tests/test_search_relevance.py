import pytest

from customer_rag import pipeline as pipeline_module
from customer_rag.config import RagConfig
from customer_rag.corpus import CorpusItem
from customer_rag.pipeline import NO_MATCH_ANSWER, RagPipeline, _keyword_score
from customer_rag.vector_store import RetrievedChunk


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return RagPipeline(RagConfig(tmp_path / "raw", tmp_path / "index", tmp_path / "model", tmp_path / "llm"))


@pytest.mark.parametrize("title", ["扫描下方二维码", "维漫斯全棉四件套"])
def test_shared_character_is_not_keyword_evidence(title):
    assert _keyword_score(
        "创维", ["创维"], brand_terms=[], category_terms=[], code_terms=[],
        title=title, location="row 1", source="manual", text=f"产品信息: {title}",
    ) == 0


def test_absent_brand_does_not_return_single_character_matches(pipeline, monkeypatch):
    pipeline.corpus.replace_all([
        CorpusItem(str(index), title, f"品牌: 其他；品类: 家居；产品信息: {title}",
                   "manual", "row 1", "", "")
        for index, title in enumerate(["扫描下方二维码", "维漫斯全棉四件套"])
    ])
    assert pipeline.keyword_search("创维", 5) == []
    monkeypatch.setattr(pipeline_module, "_run_with_timeout", lambda *_args: None)
    result = pipeline.ask("创维")
    assert result.answer == NO_MATCH_ANSWER
    assert result.sources == []


def test_timeout_fallback_rejects_unrelated_semantic_candidates(pipeline):
    source = RetrievedChunk("品牌: 海尔；产品信息: 定制流程", "manual", "扫描下方二维码", "row 1", 0.8)
    result = pipeline._fuzzy_fallback_result("创维", [source], None, [], None, deadline=0)
    assert result.answer == NO_MATCH_ANSWER
    assert result.sources == []
    assert result.timed_out


def test_timeout_fallback_keeps_complete_keyword_match(pipeline):
    source = RetrievedChunk("品牌: 创维；型号/规格: 创维电视", "manual", "创维电视", "row 1", 150)
    result = pipeline._fuzzy_fallback_result("创维", [source], None, [], None, deadline=0)
    assert "创维电视" in result.answer
    assert result.sources == [source]


def test_keyword_match_remains_searchable_without_brand_catalog(pipeline):
    pipeline.corpus.replace_all([
        CorpusItem("skyworth", "创维电视", "品牌: 创维；产品信息: 创维电视", "manual", "row 1", "", ""),
    ])
    assert [source.title for source in pipeline.keyword_search("创维", 5)] == ["创维电视"]
