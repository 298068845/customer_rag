from dataclasses import replace

from customer_rag.config import RagConfig
from customer_rag.corpus import CorpusItem
from customer_rag.pipeline import RagPipeline
from customer_rag.tencent_docs import TencentDocSubscription, save_subscriptions, subscription_output_path
from customer_rag.vector_store import RetrievedChunk


def prepare(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = RagConfig(tmp_path / "raw", tmp_path / "index", tmp_path / "model", tmp_path / "llm")
    current = TencentDocSubscription("Current", "https://docs.qq.com/sheet/current")
    old = TencentDocSubscription("Old", "https://docs.qq.com/sheet/old")
    save_subscriptions(config.index_dir / "tencent_doc_subscriptions.json", [current])
    pipeline = RagPipeline(config)
    items = [
        CorpusItem(name, name, "Brand purifier QW123", str(source), "row 1", "", "", tags=["purifier"])
        for name, source in [
            ("current", subscription_output_path(current, config.raw_data_dir)),
            ("old", subscription_output_path(old, config.raw_data_dir)),
            ("relocated", tmp_path / "old-machine" / "tencent_docs" / "Current.xlsx"),
            ("manual", tmp_path / "manual.txt"),
        ]
    ]
    pipeline.corpus.replace_all(items)
    return pipeline, current, items


def test_search_scope_excludes_deleted_and_relocated_subscriptions(tmp_path, monkeypatch):
    pipeline, _, _ = prepare(tmp_path, monkeypatch)
    assert {i.id for i in pipeline._corpus_items()} == {"current", "manual"}
    assert {r.title for r in pipeline.keyword_search("purifier", 10)} == {"current", "manual"}
    assert {i.id for i in pipeline._corpus_items_for_tags(["purifier"])} == {"current", "manual"}
    reloaded = RagPipeline(pipeline.config)
    assert {i.id for i in reloaded._corpus_items()} == {"current", "manual"}


def test_disable_invalidates_memory_and_disk_search_caches(tmp_path, monkeypatch):
    pipeline, current, _ = prepare(tmp_path, monkeypatch)
    pipeline.keyword_search("purifier", 10)
    save_subscriptions(pipeline.config.index_dir / "tencent_doc_subscriptions.json", [replace(current, enabled=False)])
    assert {r.title for r in pipeline.keyword_search("purifier", 10)} == {"manual"}
    assert {i.id for i in RagPipeline(pipeline.config)._corpus_items()} == {"manual"}


def test_vectors_from_old_subscriptions_are_filtered(tmp_path, monkeypatch):
    pipeline, _, items = prepare(tmp_path, monkeypatch)
    sources = [RetrievedChunk(i.text, i.source, i.title, i.location, 0.9) for i in items]
    assert {r.title for r in pipeline._current_sources(sources)} == {"current", "manual"}


def test_absent_subscription_config_keeps_legacy_manual_search(tmp_path, monkeypatch):
    pipeline, _, items = prepare(tmp_path, monkeypatch)
    path = pipeline.config.index_dir / "tencent_doc_subscriptions.json"
    pipeline._corpus_items()
    path.unlink()
    assert pipeline._corpus_items() == items
    assert not path.exists()


def test_water_query_cannot_return_stove_sets_via_generic_alias(tmp_path, monkeypatch):
    from customer_rag.category_config import save_category_catalog
    from customer_rag.pipeline import _filter_product_categories

    pipeline, _, items = prepare(tmp_path, monkeypatch)
    save_category_catalog({"净水": ["套装"], "烟灶": ["烟灶套装"]}, {})
    stove = replace(items[0], text="品牌: Brand；品类: 烟灶-烟灶套装；产品信息: 烟灶套装")
    water = replace(items[0], id="water", text="品牌: Brand；品类: 净水-套装；产品信息: 净水器套装")
    pipeline.corpus.replace_all([stove, water])
    for search in (pipeline.keyword_search, pipeline.category_search):
        results = search("净水器", 10)
        assert results
        assert all("品类: 净水-套装" in r.text for r in results)
    vectors = [RetrievedChunk(i.text, i.source, i.title, i.location, 0.9) for i in [stove, water]]
    assert [r.text for r in _filter_product_categories("净水器", vectors)] == [water.text]
    pipeline.corpus.replace_all([stove])
    monkeypatch.setattr(pipeline.store, "search", lambda *_a: (_ for _ in ()).throw(AssertionError("Unrelated vectors")))
    answer = pipeline.ask("净水器")
    assert not answer.sources
    assert not answer.timed_out
