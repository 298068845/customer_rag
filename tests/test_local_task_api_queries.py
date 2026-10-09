from types import SimpleNamespace

from customer_rag.config import RagConfig
from customer_rag.corpus import CorpusItem, CorpusStore
from customer_rag.local_task_api import _corpus_search_payload, _qa_payload
from customer_rag.vector_store import RetrievedChunk


def make_config(tmp_path):
    return RagConfig(
        raw_data_dir=tmp_path / "raw",
        index_dir=tmp_path / "index",
        embedding_model_path=tmp_path / "models" / "embedding",
        llm_model_path=tmp_path / "models" / "model.gguf",
        talk_data_dir=tmp_path / "talk",
    )


def test_qa_payload_exposes_answer_and_sources(monkeypatch, tmp_path) -> None:
    source = RetrievedChunk(
        text="品牌：海信；产品信息：电视",
        source="hisense.xlsx",
        title="海信电视",
        location="row 2",
        score=42.0,
        tags=["电视"],
    )
    fake_pipeline = SimpleNamespace(
        ask=lambda question, tags: SimpleNamespace(
            answer="海信结果",
            sources=[source],
            fallback=False,
            timed_out=False,
        )
    )
    monkeypatch.setattr("customer_rag.local_task_api._get_qa_pipeline", lambda config: fake_pipeline)

    payload = _qa_payload(make_config(tmp_path), "海信", ["电视"])

    assert payload["answer"] == "海信结果"
    assert payload["sources"] == [{
        "title": "海信电视",
        "source": "hisense.xlsx",
        "location": "row 2",
        "score": 42.0,
        "text": "品牌：海信；产品信息：电视",
        "tags": ["电视"],
    }]


def test_corpus_search_payload_finds_brand_text(tmp_path) -> None:
    config = make_config(tmp_path)
    CorpusStore(config.index_dir / "corpus.jsonl").replace_all([
        CorpusItem(
            id="hisense",
            title="海信电视",
            text="品牌：海信；产品信息：电视",
            source="hisense.xlsx",
            location="row 2",
            created_at="2026-10-09T00:00:00",
            updated_at="2026-10-09T01:00:00",
            tags=["电视"],
        )
    ])

    payload = _corpus_search_payload(config, {"keyword": ["海信"], "limit": ["50"]})

    assert payload["total"] == 1
    assert payload["matched"] == 1
    assert payload["rows"][0]["title"] == "海信电视"
