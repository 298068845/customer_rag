from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from customer_rag import pipeline as pipeline_module
from customer_rag import subscription_jobs as jobs
from customer_rag.config import RagConfig
from customer_rag.query_cache_invalidation import invalidate_query_cache
from customer_rag.tencent_docs import TencentDocSubscription, save_subscriptions, subscription_output_path


def write_cache(index_dir: Path, key: str, sources: list[str] | None) -> Path:
    path = index_dir / "query_cache" / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"answer": "cached"}
    if sources is not None:
        payload["source_paths"] = sources
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_changed_document_removes_related_and_legacy_caches_only(tmp_path):
    hisense = tmp_path / "raw" / "海信.xlsx"
    unrelated = tmp_path / "raw" / "海尔.xlsx"
    related = write_cache(tmp_path, "related", [str(hisense)])
    mixed = write_cache(tmp_path, "mixed", [str(hisense), str(unrelated)])
    other = write_cache(tmp_path, "other", [str(unrelated)])
    legacy = write_cache(tmp_path, "legacy", None)
    corrupt = write_cache(tmp_path, "corrupt", None)
    corrupt.write_text("broken JSON", encoding="utf-8")

    assert invalidate_query_cache(tmp_path, [str(hisense).replace("\\", "/").upper()]) == 4
    assert other.exists()
    assert not any(path.exists() for path in [related, mixed, legacy, corrupt])


def test_full_invalidation_is_limited_to_query_cache_directory(tmp_path):
    cached = write_cache(tmp_path, "cached", [str(tmp_path / "file.xlsx")])
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("corpus", encoding="utf-8")
    assert invalidate_query_cache(tmp_path) == 1
    assert not cached.exists()
    assert corpus.read_text(encoding="utf-8") == "corpus"


def test_empty_update_does_not_clear_caches(tmp_path):
    cached = write_cache(tmp_path, "cached", None)
    assert invalidate_query_cache(tmp_path, []) == 0
    assert cached.exists()


def test_clear_corpus_invalidates_all_search_layers(tmp_path):
    from customer_rag.corpus import CorpusStore
    from customer_rag.vector_store import VectorStore

    corpus = CorpusStore(tmp_path / "corpus.jsonl")
    corpus.add("Old", "Old product")
    cached = write_cache(tmp_path, "old-answer", ["old.xlsx"])
    files = [
        "corpus.parsed.pkl", "quick_search_cache.pkl", "faiss.index", "chunks.jsonl",
        "faiss.old.index", "chunks.old.jsonl", "embedding_cache.npz", "raw_parse_manifest.json",
    ]
    for name in files:
        (tmp_path / name).write_bytes(b"old data")
    vectors = VectorStore(tmp_path, tmp_path / "model")
    vectors.index = object()
    vectors._loaded_generation = "old"
    corpus.clear()
    assert not cached.exists()
    assert not any((tmp_path / name).exists() for name in files)
    assert vectors.search("old product", 5) == []
    assert corpus.list_items() == []


def test_partial_delete_invalidates_vectors_without_losing_remaining_corpus(tmp_path):
    from customer_rag.corpus import CorpusStore
    from customer_rag.vector_store import VectorStore

    corpus = CorpusStore(tmp_path / "corpus.jsonl")
    old = corpus.add("Old", "Old product")
    current = corpus.add("Current", "Current product")
    cached = write_cache(tmp_path, "old", [old.source])
    corpus.delete_many({old.id})
    assert not cached.exists()
    assert [i.id for i in corpus.list_items()] == [current.id]
    assert VectorStore(tmp_path, tmp_path / "model").search("Old product", 5) == []


@pytest.mark.parametrize("scope", ["pending", "full"])
def test_subscription_publish_clears_cache_before_rebuilding_vectors(tmp_path, monkeypatch, scope):
    config, subscription, subscription_path, source = prepare_job(tmp_path, monkeypatch, scope)
    related = write_cache(config.index_dir, "related", [str(source)])
    other = write_cache(config.index_dir, "other", [str(tmp_path / "other.xlsx")])
    events = []

    class FakePipeline:
        def __init__(self, _config):
            pass

        def replace_files_with_tags(self, path_tags, **_kwargs):
            assert path_tags == [(source, subscription.tags)]
            return self.publish()

        def rebuild_corpus_from_raw(self, **_kwargs):
            return self.publish()

        def publish(self):
            assert related.exists()
            events.append("published")
            return {"documents": 1, "items": 1}

        def rebuild_index(self, **_kwargs):
            assert not related.exists()
            assert other.exists() is (scope != "full")
            events.append("vectors")
            return 1

    monkeypatch.setattr(pipeline_module, "RagPipeline", FakePipeline)
    jobs._run_subscription_job(config, subscription_path, [subscription], "cookie", "cache-update")

    state = jobs.read_job_state(config)
    assert state.status == "completed", state.message
    assert events == ["published", "vectors"]
    assert any("查询缓存" in entry for entry in state.logs)


@pytest.mark.parametrize("outcome", ["unchanged", "download_failed", "parse_failed"])
def test_subscription_without_published_update_keeps_cache(tmp_path, monkeypatch, outcome):
    config, subscription, subscription_path, source = prepare_job(tmp_path, monkeypatch, "pending")
    cached = write_cache(config.index_dir, "related", [str(source)])
    if outcome == "unchanged":
        monkeypatch.setattr(jobs, "fetch_subscription_last_modified", lambda *_a, **_kw: subscription.last_modified)
    elif outcome == "download_failed":
        def download(*_a, **_kw):
            raise RuntimeError("download unavailable")
        monkeypatch.setattr(jobs, "download_subscription", download)
    else:
        class FailedParse:
            def __init__(self, _config):
                pass

            def replace_files_with_tags(self, *_a, **_kw):
                raise RuntimeError("parse unavailable")
        monkeypatch.setattr(pipeline_module, "RagPipeline", FailedParse)

    jobs._run_subscription_job(config, subscription_path, [subscription], "cookie", "cache-update")

    state = jobs.read_job_state(config)
    assert state.status == ("error" if outcome == "parse_failed" else "completed"), state.message
    assert cached.exists()


def prepare_job(tmp_path, monkeypatch, scope):
    config = RagConfig(
        raw_data_dir=tmp_path / "raw", index_dir=tmp_path / "user-index",
        embedding_model_path=tmp_path / "embedding", llm_model_path=tmp_path / "model.gguf",
    )
    subscription = TencentDocSubscription(
        "海信", "https://docs.qq.com/sheet/test", tags=["海信"], last_modified="2026-10-07 12:00:00",
    )
    subscription_path = config.index_dir / "tencent_doc_subscriptions.json"
    save_subscriptions(subscription_path, [subscription])
    source = subscription_output_path(subscription, config.raw_data_dir)
    source.parent.mkdir(parents=True)
    source.write_bytes(b"subscription content")
    jobs._write_state(config, jobs.SubscriptionJobState(job_id="cache-update", total=1))
    monkeypatch.setattr(jobs, "configure_logging", lambda *_a, **_kw: None)
    monkeypatch.setattr(jobs, "log_event", lambda *_a, **_kw: None)
    monkeypatch.setattr(jobs, "log_exception", lambda *_a, **_kw: None)
    monkeypatch.setattr(jobs, "release", lambda *_a, **_kw: None)
    monkeypatch.setattr(jobs, "fetch_subscription_page", lambda *_a, **_kw: object())
    monkeypatch.setattr(jobs, "fetch_subscription_last_modified", lambda *_a, **_kw: "2026-10-08 12:00:00")
    monkeypatch.setattr(jobs, "download_subscription", lambda *_a, **_kw: source)
    monkeypatch.setattr(jobs, "sync_subscription_brand_replies", lambda *_a, **_kw: 0)
    monkeypatch.setattr(jobs, "read_coordinator_state", lambda *_a: SimpleNamespace(subscription_import_scope=scope))
    return config, subscription, subscription_path, source
