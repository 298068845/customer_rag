from dataclasses import asdict
import pickle

import pytest

from customer_rag import corpus as corpus_module
from customer_rag.config import RagConfig
from customer_rag.corpus import CorpusItem, CorpusStore, _file_signature
from customer_rag.pipeline import RagPipeline


@pytest.fixture
def query_pipeline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pipeline = RagPipeline(RagConfig(tmp_path / "raw", tmp_path / "index", tmp_path / "model", tmp_path / "llm"))
    item = CorpusItem(
        "skyworth", "创维电视", "品牌: 创维；品类: 电视；型号/规格: SW123",
        "manual", "row 1", "", "", tags=["创维", "电视"],
        attributes={"brand": "创维"}, image_paths=["product.png"],
    )
    pipeline.corpus.replace_all([item])
    return pipeline, item


def test_query_and_cache_survive_stale_corpus_class(query_pipeline, monkeypatch):
    pipeline, item = query_pipeline
    # A reload replaces the module's class while a cached pipeline retains old instances.
    monkeypatch.setattr(corpus_module, "CorpusItem", type("ReloadedCorpusItem", (CorpusItem,), {}))
    monkeypatch.setattr(corpus_module, "_item_from_payload", lambda payload: CorpusItem(**payload))
    with pytest.raises(pickle.PicklingError):
        pickle.dumps(item)

    assert pipeline.ask("创维").sources
    with pipeline.corpus._cache_path().open("rb") as fp:
        assert pickle.load(fp)["items"] == [asdict(item)]
    with pipeline._quick_search_cache_path().open("rb") as fp:
        payload = pickle.load(fp)
    assert payload["tag_index"]["创维"] == [item.id]

    reloaded = RagPipeline(pipeline.config)
    assert reloaded.ask("创维").sources
    assert reloaded.corpus.list_items() == [item]
    assert reloaded._tag_index_cache["创维"][0] is reloaded._corpus_cache[0]
    assert not list(pipeline.config.index_dir.glob("*.tmp"))


@pytest.mark.parametrize("error", [pickle.PicklingError("reload"), OSError("disk unavailable")])
def test_optional_cache_write_failure_does_not_break_query(query_pipeline, monkeypatch, error):
    pipeline, item = query_pipeline

    def fail_dump(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(pickle, "dump", fail_dump)
    result = pipeline.ask("创维")
    assert result.sources
    assert pipeline.corpus.list_items() == [item]
    assert not list(pipeline.config.index_dir.glob("*.tmp"))


@pytest.mark.parametrize("items", [["broken"], [{"unexpected": "field"}]])
def test_invalid_parsed_cache_is_rebuilt_from_source(query_pipeline, items):
    pipeline, item = query_pipeline
    path = pipeline.corpus._cache_path()
    with path.open("wb") as fp:
        pickle.dump({"version": corpus_module.CORPUS_CACHE_VERSION,
                     "signature": _file_signature(pipeline.corpus.path), "items": items}, fp)
    assert CorpusStore(pipeline.corpus.path).list_items() == [item]


@pytest.mark.parametrize("version", [1, 2])
def test_old_parsed_cache_is_rebuilt(query_pipeline, version):
    pipeline, item = query_pipeline
    with pipeline.corpus._cache_path().open("wb") as fp:
        pickle.dump({"version": version, "signature": _file_signature(pipeline.corpus.path), "items": [item]}, fp)
    assert pipeline.ask("创维").sources
    with pipeline.corpus._cache_path().open("rb") as fp:
        assert pickle.load(fp)["items"] == [asdict(item)]


def test_unknown_quick_cache_ids_are_rebuilt(query_pipeline):
    pipeline, item = query_pipeline
    assert pipeline.ask("创维").sources
    path = pipeline._quick_search_cache_path()
    with path.open("rb") as fp:
        payload = pickle.load(fp)
    payload["tag_index"]["创维"] = ["deleted-item"]
    with path.open("wb") as fp:
        pickle.dump(payload, fp)
    reloaded = RagPipeline(pipeline.config)
    assert reloaded.ask("创维").sources
    assert reloaded._tag_index_cache["创维"][0].id == item.id
