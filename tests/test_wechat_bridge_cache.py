from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from customer_rag import config as config_module
from customer_rag import pipeline as pipeline_module
from customer_rag import wechat_bridge as bridge
from customer_rag.config import RagConfig
from customer_rag.vector_store import RetrievedChunk


def make_config(root: Path) -> RagConfig:
    return RagConfig(
        raw_data_dir=root / "raw", index_dir=root / "user-index",
        embedding_model_path=root / "embedding", llm_model_path=root / "model.gguf",
    )


def make_source(root: Path) -> RetrievedChunk:
    return RetrievedChunk(
        text="品牌：海信；产品信息：电视A；商品链接：https://example.com/item",
        source=str(root / "海信.xlsx"), title="海信电视A", location="row 1", score=150,
    )


def cache_key(root: Path, config: RagConfig) -> str:
    return bridge.query_cache_key(
        root, config=config, question="海信", effective_question="海信", selected_brand="",
        tags=[], top_k=config.top_k, system_prompt="prompt",
    )


def test_cache_tracks_configured_corpus_and_config_instead_of_installation_files(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    config.index_dir.mkdir()
    corpus = config.index_dir / "corpus.jsonl"
    corpus.write_text("old", encoding="utf-8")
    config_path = tmp_path / "user-config" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text("top_k: 5", encoding="utf-8")
    monkeypatch.setattr(bridge, "resolve_config_path", lambda: config_path)
    original = cache_key(tmp_path, config)

    decoy = tmp_path / "data" / "index" / "corpus.jsonl"
    decoy.parent.mkdir(parents=True)
    decoy.write_text("installation corpus", encoding="utf-8")
    assert cache_key(tmp_path, config) == original

    corpus.write_text("updated subscription document", encoding="utf-8")
    updated = cache_key(tmp_path, config)
    assert updated != original
    config_path.write_text("top_k: 10", encoding="utf-8")
    assert cache_key(tmp_path, config) != updated
    assert cache_key(tmp_path, replace(config, index_dir=tmp_path / "another-index")) != updated


def test_success_cache_expires_and_records_document_dependencies(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    monkeypatch.setattr(bridge.time, "time", lambda: 1000)
    bridge.write_query_cache(tmp_path, "result", answer="电视A", brands="海信", sources=[source])
    payload = bridge.read_query_cache(tmp_path, "result")
    assert payload is not None
    assert payload["source_paths"] == [str(Path(source.source).resolve())]
    assert payload["sources"] == 1

    monkeypatch.setattr(bridge.time, "time", lambda: 1000 + bridge.QUERY_CACHE_TTL_SECONDS + 1)
    assert bridge.read_query_cache(tmp_path, "result") is None


def test_subscription_changes_invalidate_query_answers(tmp_path, monkeypatch):
    from customer_rag.tencent_docs import TencentDocSubscription, save_subscriptions

    config = make_config(tmp_path)
    monkeypatch.setattr(bridge, "resolve_config_path", lambda: tmp_path / "config.yaml")
    path = config.index_dir / "tencent_doc_subscriptions.json"
    subscription = TencentDocSubscription("Current", "https://docs.qq.com/sheet/current")
    save_subscriptions(path, [subscription])
    before = cache_key(tmp_path, config)
    save_subscriptions(path, [replace(subscription, enabled=False)])
    assert cache_key(tmp_path, config) != before


@pytest.mark.parametrize("filename", ["vector_index_manifest.json", "faiss.index", "chunks.jsonl"])
def test_vector_publish_invalidates_answers_cached_during_subscription_rebuild(tmp_path, monkeypatch, filename):
    config = make_config(tmp_path)
    config.index_dir.mkdir()
    monkeypatch.setattr(bridge, "resolve_config_path", lambda: tmp_path / "config.yaml")
    index_file = config.index_dir / filename
    index_file.write_bytes(b"old vector generation")
    during_rebuild = cache_key(tmp_path, config)
    index_file.write_bytes(b"new vector generation after subscription update")
    assert cache_key(tmp_path, config) != during_rebuild


@pytest.mark.parametrize("empty,fallback,timed_out", [(True, False, False), (False, True, False), (False, False, True)])
def test_empty_fallback_and_timeout_results_are_not_cached(tmp_path, empty, fallback, timed_out):
    bridge.write_query_cache(
        tmp_path, "bad", answer="reply", brands="海信",
        sources=[] if empty else [make_source(tmp_path)], fallback=fallback, timed_out=timed_out,
    )
    assert bridge.read_query_cache(tmp_path, "bad") is None
    assert not bridge.query_cache_path(tmp_path, "bad").exists()


def test_previous_cache_version_is_ignored(tmp_path):
    path = bridge.query_cache_path(tmp_path, "old")
    path.parent.mkdir()
    path.write_text(json.dumps({"version": 11, "answer": "没有做这款呢，看看其他"}), encoding="utf-8")
    assert bridge.read_query_cache(tmp_path, "old") is None


def test_no_results_clear_previous_and_selected_brand(tmp_path):
    path = tmp_path / "brands.txt"
    path.write_text("海信\n容声", encoding="utf-8")
    bridge.write_query_brands(path, [], "海信", answer="没有做这款呢，看看其他")
    assert path.read_text(encoding="utf-8") == ""


def run_bridge(tmp_path, monkeypatch, result, *, selected_brand="", update_during_query=False,
               initial_brands="海信\n容声"):
    monkeypatch.chdir(tmp_path)
    config = make_config(tmp_path)
    config.index_dir.mkdir()
    corpus = config.index_dir / "corpus.jsonl"
    corpus.write_text("old", encoding="utf-8")
    prompt_path = config.index_dir / "prompt_settings.json"
    prompt_path.write_text(json.dumps({"system_prompt": "configured prompt"}), encoding="utf-8")
    install_prompt = tmp_path / "data" / "index" / "prompt_settings.json"
    install_prompt.parent.mkdir(parents=True)
    install_prompt.write_text(json.dumps({"system_prompt": "wrong install prompt"}), encoding="utf-8")
    question = tmp_path / "question.txt"
    question.write_text("海信", encoding="utf-8")
    output = tmp_path / "answer.txt"
    brands = tmp_path / "brands.txt"
    brands.write_text(initial_brands, encoding="utf-8")
    calls = []

    class FakePipeline:
        def __init__(self, _config):
            pass

        def ask(self, question, **kwargs):
            calls.append((question, kwargs))
            if update_during_query:
                corpus.write_text("new subscription document", encoding="utf-8")
            return result

    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr(bridge, "resolve_config_path", lambda: tmp_path / "user-config.yaml")
    monkeypatch.setattr(pipeline_module, "RagPipeline", FakePipeline)
    monkeypatch.setattr(bridge, "configure_logging", lambda *a, **kw: None)
    monkeypatch.setattr(bridge, "log_event", lambda *a, **kw: None)
    monkeypatch.setattr(bridge, "category_brands", lambda: {})
    monkeypatch.setattr(bridge, "category_aliases", lambda: {})
    argv = ["wechat_bridge", "--project-root", str(tmp_path), "--question-file", str(question),
            "--output-file", str(output), "--brands-file", str(brands)]
    if selected_brand:
        argv += ["--brand", selected_brand]
    monkeypatch.setattr(bridge.sys, "argv", argv)
    assert bridge.main() == 0
    return config, output, brands, calls


@pytest.mark.parametrize("timed_out", [False, True])
def test_bridge_distinguishes_empty_results_from_timeout_and_clears_brands(tmp_path, monkeypatch, timed_out):
    result = SimpleNamespace(answer="没有做这款呢，看看其他", sources=[], fallback=True, timed_out=timed_out)
    config, output, brands, _calls = run_bridge(tmp_path, monkeypatch, result, selected_brand="海信")
    text = output.read_text(encoding="utf-8")
    assert (bridge.TIMEOUT_CONTROL_MARKER in text) is timed_out
    assert ("查询超时，请稍后重试" in text) is timed_out
    assert brands.read_text(encoding="utf-8") == ""
    assert not bridge.query_cache_dir(config.index_dir).exists()


def test_bridge_uses_same_configured_prompt_for_query_and_cache_hit(tmp_path, monkeypatch):
    result = SimpleNamespace(answer="海信电视A", sources=[make_source(tmp_path)], fallback=False, timed_out=False)
    config, output, _brands, calls = run_bridge(tmp_path, monkeypatch, result, selected_brand="海信", initial_brands="海信")
    assert calls == [("海信\n指定品牌：海信", {"system_prompt": "configured prompt" + bridge.DEFAULT_PROMPT_SUFFIX, "tags": []})]
    assert bridge.query_cache_dir(config.index_dir).is_dir()
    assert bridge.main() == 0
    assert len(calls) == 1
    assert output.read_text(encoding="utf-8") == "海信电视A"


def test_query_in_progress_during_subscription_update_is_not_cached(tmp_path, monkeypatch):
    result = SimpleNamespace(answer="海信电视A", sources=[make_source(tmp_path)], fallback=False, timed_out=False)
    config, _output, _brands, _calls = run_bridge(tmp_path, monkeypatch, result, update_during_query=True)
    assert not bridge.query_cache_dir(config.index_dir).exists()
