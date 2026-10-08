from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

import pytest

from customer_rag.category_config import category_catalog, save_category_catalog
from customer_rag.config import RagConfig
from customer_rag.corpus import CorpusItem, CorpusStore
from customer_rag.loaders import LoadedDocument
from customer_rag.pipeline import RagPipeline
from customer_rag.subscription_jobs import SubscriptionJobState, _run_subscription_job, _write_state, read_job_state
from customer_rag.talk_rag import (
    BrandAliasRule,
    BrandReplyRule,
    BrandSaleStatusRule,
    RealtimeTalkConfig,
    TalkRagEngine,
    TalkRagStore,
    render_brand_reply,
    render_keyword_reply,
    sync_subscription_brand_replies,
)
from customer_rag.tencent_docs import TencentDocSubscription, save_subscriptions, subscription_output_path


MIDEA = "\u7f8e\u7684"
TOSHIBA = "\u4e1c\u829d"
BRAND = "\u54c1\u724c"


@pytest.fixture(autouse=True)
def isolate_category_catalog(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def make_config(root: Path) -> RagConfig:
    return RagConfig(
        raw_data_dir=root / "raw",
        index_dir=root / "index",
        talk_data_dir=root / "talk",
        embedding_model_path=root / "model",
        llm_model_path=root / "model.gguf",
    )


def product_text(brand: str) -> str:
    return f"{BRAND}: {brand}\uff1b\u54c1\u7c7b: Sample\uff1b\u4ea7\u54c1\u4fe1\u606f: Model-123"


def item_for(subscription: TencentDocSubscription, config: RagConfig, brand: str) -> CorpusItem:
    return CorpusItem(
        id=brand,
        title="Product",
        text=product_text(brand),
        source=str(subscription_output_path(subscription, config.raw_data_dir)),
        location="row 2",
        created_at="",
        updated_at="",
    )


def seed_store(config: RagConfig) -> TalkRagStore:
    store = TalkRagStore(config.talk_data_dir)
    store.save_realtime_config(
        RealtimeTalkConfig(
            brand_reply_rules=[
                BrandReplyRule("midea", BRAND, MIDEA, ["old reply"], "keep this supplement"),
                BrandReplyRule("manual", BRAND, "Manual", ["custom reply"], "manual supplement"),
            ],
            brand_reply_rules_initialized=True,
            today_template="keep today template",
        )
    )
    return store


def test_sync_updates_existing_rules_and_adds_parsed_brands(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    subscription = TencentDocSubscription("Current catalog", "https://docs.qq.com/sheet/current?tab=sheet1")
    items = [item_for(subscription, config, brand) for brand in (MIDEA, TOSHIBA)]

    assert store.sync_subscription_brand_replies([subscription], config.raw_data_dir, items) == 3

    saved = store.load_realtime_config()
    rules = {rule.keyword: rule for rule in saved.brand_reply_rules}
    expected = [f"\u3010{subscription.name}\u3011", subscription.url]
    assert rules[MIDEA].reply_terms == expected
    assert rules[MIDEA].id == "midea"
    assert rules[MIDEA].supplemental_reply == "keep this supplement"
    assert rules[TOSHIBA].reply_terms == expected
    assert rules["Manual"].reply_terms == []
    assert rules["Manual"].id == "manual"
    assert rules["Manual"].supplemental_reply == "manual supplement"
    assert render_brand_reply("Manual", saved) == ""
    assert saved.today_template == "keep today template"
    assert render_brand_reply(MIDEA, saved).endswith("\n---\nkeep this supplement")


def test_sync_combines_distinct_catalogs_and_deduplicates_urls(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    first = TencentDocSubscription("First", "https://docs.qq.com/sheet/first")
    second = TencentDocSubscription("Second", "https://docs.qq.com/sheet/second")
    items = [item_for(subscription, config, MIDEA) for subscription in (first, first, second)]

    assert store.sync_subscription_brand_replies([first, first, second], config.raw_data_dir, items) == 2

    rule = store.load_realtime_config().brand_reply_rules[0]
    assert rule.reply_terms == ["\u3010First\u3011", first.url, "---", "\u3010Second\u3011", second.url]
    signature = store.realtime_path.stat().st_mtime_ns
    assert store.sync_subscription_brand_replies([first, first, second], config.raw_data_dir, items) == 0
    assert store.realtime_path.stat().st_mtime_ns == signature


def test_sync_uses_explicit_aliases_and_case_insensitive_brands(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    saved = store.load_realtime_config()
    store.save_realtime_config(
        replace(
            saved,
            brand_reply_rules=[*saved.brand_reply_rules, BrandReplyRule("koey", BRAND, "KOEY", ["old"])],
            brand_alias_rules=[BrandAliasRule("alias", MIDEA, ["Midea"])],
            sale_status_rules=[BrandSaleStatusRule("status", TOSHIBA, ["Toshiba-JD"])],
        )
    )
    subscription = TencentDocSubscription("Current", "https://docs.qq.com/sheet/current")
    items = [item_for(subscription, config, brand) for brand in ("Midea", "koey", "Toshiba-JD")]

    assert store.sync_subscription_brand_replies([subscription], config.raw_data_dir, items) == 4

    rules = {rule.keyword: rule for rule in store.load_realtime_config().brand_reply_rules}
    for brand in (MIDEA, "KOEY", TOSHIBA):
        assert subscription.url in rules[brand].reply_terms
    assert "koey" not in rules
    assert "Midea" not in rules
    assert "Toshiba-JD" not in rules


def test_sync_ignores_disabled_sources_unrelated_files_and_sheet_notices(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    active = TencentDocSubscription("Active", "https://docs.qq.com/sheet/active")
    disabled = TencentDocSubscription("Disabled", "https://docs.qq.com/sheet/disabled", enabled=False)
    items = [
        item_for(disabled, config, MIDEA),
        replace(item_for(active, config, MIDEA), source=str(tmp_path / "other.xlsx")),
        replace(item_for(active, config, "Notice"), text=f"{BRAND}: Notice"),
    ]
    assert store.sync_subscription_brand_replies([active, disabled], config.raw_data_dir, items) == 2
    saved = store.load_realtime_config()
    assert {rule.keyword for rule in saved.brand_reply_rules} == {MIDEA, "Manual"}
    assert all(rule.reply_terms == [] for rule in saved.brand_reply_rules)
    assert saved.brand_reply_rules[0].supplemental_reply == "keep this supplement"
    assert store.sync_subscription_brand_replies([active, disabled], config.raw_data_dir, items) == 0


def test_sync_reads_existing_corpus_without_downloading(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    subscription = TencentDocSubscription("Current", "https://docs.qq.com/sheet/current")
    save_subscriptions(config.index_dir / "tencent_doc_subscriptions.json", [subscription])
    CorpusStore(config.index_dir / "corpus.jsonl").replace_all([item_for(subscription, config, MIDEA)])

    assert sync_subscription_brand_replies(config) == 2
    assert subscription.url in store.load_realtime_config().brand_reply_rules[0].reply_terms


def test_sync_does_not_seed_subscriptions_when_no_file_exists(tmp_path: Path) -> None:
    config = make_config(tmp_path)

    assert sync_subscription_brand_replies(config) == 0
    assert not config.index_dir.exists()
    assert not config.talk_data_dir.exists()


def test_deleted_subscription_clears_previously_linked_reply(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    subscription = TencentDocSubscription("Current", "https://docs.qq.com/sheet/current")
    items = [item_for(subscription, config, MIDEA)]
    store.sync_subscription_brand_replies([subscription], config.raw_data_dir, items)

    assert store.sync_subscription_brand_replies([], config.raw_data_dir, items) == 1
    saved = store.load_realtime_config()
    assert saved.brand_reply_rules[0].reply_terms == []
    assert saved.brand_reply_rules[0].id == "midea"
    assert saved.brand_reply_rules[0].supplemental_reply == "keep this supplement"
    assert render_brand_reply(MIDEA, saved) == ""
    assert store.sync_subscription_brand_replies([], config.raw_data_dir, items) == 0


def test_empty_subscription_list_clears_brand_replies_only(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    saved = store.load_realtime_config()
    category_rule = BrandReplyRule("category", "\u54c1\u7c7b", "Category", ["category reply"])
    store.save_realtime_config(replace(saved, brand_reply_rules=[*saved.brand_reply_rules, category_rule]))
    save_subscriptions(config.index_dir / "tencent_doc_subscriptions.json", [])

    assert sync_subscription_brand_replies(config, []) == 2
    saved = store.load_realtime_config()
    assert all(rule.reply_terms == [] for rule in saved.brand_reply_rules[:2])
    assert asdict(saved.brand_reply_rules[2]) == asdict(category_rule)
    assert saved.today_template == "keep today template"


def test_blank_subscription_url_cannot_keep_a_brand_reply(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    subscription = TencentDocSubscription("Current", " ")
    items = [item_for(subscription, config, MIDEA)]

    assert store.sync_subscription_brand_replies([subscription], config.raw_data_dir, items) == 2
    assert store.load_realtime_config().brand_reply_rules[0].reply_terms == []


def test_brand_removed_from_catalog_has_reply_cleared(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    subscription = TencentDocSubscription("Current", "https://docs.qq.com/sheet/current")
    store.sync_subscription_brand_replies([subscription], config.raw_data_dir, [item_for(subscription, config, MIDEA)])

    assert store.sync_subscription_brand_replies(
        [subscription], config.raw_data_dir, [item_for(subscription, config, TOSHIBA)],
    ) == 2
    saved = store.load_realtime_config()
    rules = {rule.keyword: rule for rule in saved.brand_reply_rules}
    assert rules[MIDEA].reply_terms == []
    assert subscription.url in rules[TOSHIBA].reply_terms


def test_engine_reloads_rules_updated_by_background_sync(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    engine = TalkRagEngine(store)
    assert engine.realtime_config().brand_reply_rules[0].reply_terms == ["old reply"]
    subscription = TencentDocSubscription("Current", "https://docs.qq.com/sheet/current")
    store.sync_subscription_brand_replies([subscription], config.raw_data_dir, [item_for(subscription, config, MIDEA)])

    assert subscription.url in engine.realtime_config().brand_reply_rules[0].reply_terms


def test_unchanged_subscription_updates_rules_from_cached_products(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    subscription = TencentDocSubscription(
        "Current", "https://docs.qq.com/sheet/current", last_modified="2026-06-11T12:00:00+08:00",
    )
    subscriptions_path = config.index_dir / "tencent_doc_subscriptions.json"
    save_subscriptions(subscriptions_path, [subscription])
    path = subscription_output_path(subscription, config.raw_data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"cached")
    CorpusStore(config.index_dir / "corpus.jsonl").replace_all([item_for(subscription, config, MIDEA)])
    _write_state(config, SubscriptionJobState(job_id="cached-sync", total=1))
    with patch("customer_rag.subscription_jobs.fetch_subscription_page", return_value=object()), patch(
        "customer_rag.subscription_jobs.fetch_subscription_last_modified", return_value=subscription.last_modified
    ), patch("customer_rag.subscription_jobs.download_subscription") as download:
        _run_subscription_job(config, subscriptions_path, [subscription], "cookie", "cached-sync")

    state = read_job_state(config)
    assert state.status == "completed", state.message
    assert state.skipped == 1
    assert state.downloaded == 0
    download.assert_not_called()
    assert subscription.url in store.load_realtime_config().brand_reply_rules[0].reply_terms


def test_sync_uses_same_platform_normalization_as_product_catalog(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    subscription = TencentDocSubscription("Current", "https://docs.qq.com/sheet/current")
    items = [item_for(subscription, config, f"{brand}-\u5929\u732b") for brand in (MIDEA, TOSHIBA)]

    assert store.sync_subscription_brand_replies([subscription], config.raw_data_dir, items) == 3
    rules = {rule.keyword: rule for rule in store.load_realtime_config().brand_reply_rules}
    assert subscription.url in rules[MIDEA].reply_terms
    assert subscription.url in rules[TOSHIBA].reply_terms


def test_incremental_parse_synchronizes_brand_replies(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    subscription = TencentDocSubscription("Current", "https://docs.qq.com/sheet/current")
    save_subscriptions(config.index_dir / "tencent_doc_subscriptions.json", [subscription])
    path = subscription_output_path(subscription, config.raw_data_dir)
    document = LoadedDocument(product_text(MIDEA), str(path), "Product", "row 2")
    with patch("customer_rag.pipeline.load_document_file", return_value=[document]), patch(
        "customer_rag.pipeline.add_category_terms", return_value=0
    ):
        stats = RagPipeline(config).replace_files_with_tags([(path, [])], rebuild_index=False)

    assert stats["synced_brand_replies"] == 2
    assert subscription.url in store.load_realtime_config().brand_reply_rules[0].reply_terms


def test_full_parse_synchronizes_brand_replies(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    subscription = TencentDocSubscription("Current", "https://docs.qq.com/sheet/current")
    save_subscriptions(config.index_dir / "tencent_doc_subscriptions.json", [subscription])
    path = subscription_output_path(subscription, config.raw_data_dir)
    document = LoadedDocument(product_text(MIDEA), str(path), "Product", "row 2")
    with patch("customer_rag.pipeline.load_documents", return_value=[document]), patch(
        "customer_rag.pipeline.list_supported_files", return_value=[]
    ), patch(
        "customer_rag.pipeline.add_category_terms", return_value=0
    ):
        stats = RagPipeline(config).rebuild_corpus_from_raw(rebuild_index=False, force=True)

    assert stats["synced_brand_replies"] == 2
    assert subscription.url in store.load_realtime_config().brand_reply_rules[0].reply_terms


def test_category_reply_filters_each_subscription_for_the_same_brand(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    water = TencentDocSubscription("Water", "https://docs.qq.com/sheet/water")
    lights = TencentDocSubscription("Lights", "https://docs.qq.com/sheet/lights")
    items = [
        replace(item_for(water, config, MIDEA), text=product_text(MIDEA).replace("Sample", "净水-厨下")),
        replace(item_for(lights, config, MIDEA), text=product_text(MIDEA).replace("Sample", "照明")),
    ]
    store.sync_subscription_brand_replies([water, lights], config.raw_data_dir, items)
    saved = store.load_realtime_config()

    with patch("customer_rag.talk_rag.category_aliases", return_value={"净水器": ["净水-厨下"]}):
        for question in ("~搜净水器", f"{MIDEA}净水器"):
            answer = render_keyword_reply(question, saved, include_index=False)
            assert water.url in answer
            assert lights.url not in answer
            assert "keep this supplement" in answer
    brand_answer = render_keyword_reply(MIDEA, saved, include_index=False)
    assert water.url in brand_answer and lights.url in brand_answer


def test_old_category_brand_mapping_cannot_return_unrelated_active_subscription(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    active = TencentDocSubscription("Lights", "https://docs.qq.com/sheet/lights")
    old = TencentDocSubscription("Old water", "https://docs.qq.com/sheet/old")
    items = [
        replace(item_for(active, config, MIDEA), text=product_text(MIDEA).replace("Sample", "照明")),
        replace(item_for(old, config, MIDEA), text=product_text(MIDEA).replace("Sample", "净水器")),
    ]
    store.sync_subscription_brand_replies([active], config.raw_data_dir, items)

    with patch("customer_rag.talk_rag.category_aliases", return_value={"净水器": []}), patch(
        "customer_rag.talk_rag.category_brands", return_value={"净水器": [MIDEA]},
    ):
        saved = store.load_realtime_config()
        assert render_keyword_reply("净水器", saved, include_index=False) == ""
        assert TalkRagEngine(store).ask("~搜净水器").score == 0


def test_subscription_category_is_specific_to_product_brand(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    subscription = TencentDocSubscription("Mixed", "https://docs.qq.com/sheet/mixed")
    items = [
        replace(item_for(subscription, config, MIDEA), text=product_text(MIDEA).replace("Sample", "照明")),
        replace(item_for(subscription, config, TOSHIBA), text=product_text(TOSHIBA).replace("Sample", "净水器")),
    ]
    store.sync_subscription_brand_replies([subscription], config.raw_data_dir, items)
    saved = store.load_realtime_config()
    assert render_keyword_reply(f"{MIDEA}净水器", saved, include_index=False) == ""
    assert subscription.url in render_keyword_reply(f"{TOSHIBA}净水器", saved, include_index=False)


def test_generic_water_alias_does_not_match_other_product_sets(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    subscription = TencentDocSubscription("Knife sets", "https://docs.qq.com/sheet/knives")
    item = replace(item_for(subscription, config, MIDEA), text=product_text(MIDEA).replace("Sample", "刀具套装"))
    store.sync_subscription_brand_replies([subscription], config.raw_data_dir, [item])
    with patch("customer_rag.talk_rag.category_aliases", return_value={"净水": ["套装"]}):
        assert render_keyword_reply("净水器", store.load_realtime_config(), include_index=False) == ""


def test_category_change_refreshes_metadata_even_when_brand_url_stays_the_same(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    subscription = TencentDocSubscription("Mixed", "https://docs.qq.com/sheet/mixed")
    item = replace(item_for(subscription, config, MIDEA), text=product_text(MIDEA).replace("Sample", "净水器"))
    store.sync_subscription_brand_replies([subscription], config.raw_data_dir, [item])
    engine = TalkRagEngine(store)
    assert subscription.url in render_keyword_reply("净水器", engine.realtime_config(), include_index=False)
    assert store.sync_subscription_brand_replies(
        [subscription], config.raw_data_dir, [replace(item, text=item.text.replace("净水器", "照明"))],
    ) == 1
    assert render_keyword_reply("净水器", engine.realtime_config(), include_index=False) == ""


def test_category_from_current_subscription_does_not_need_static_catalog(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = seed_store(config)
    subscription = TencentDocSubscription("New", "https://docs.qq.com/sheet/new")
    store.sync_subscription_brand_replies([subscription], config.raw_data_dir, [item_for(subscription, config, MIDEA)])
    with patch("customer_rag.talk_rag.category_aliases", return_value={}), patch(
        "customer_rag.talk_rag.category_brands", return_value={},
    ), patch("customer_rag.talk_rag._indexed_category_brands", return_value={}):
        assert subscription.url in render_keyword_reply("Sample", store.load_realtime_config(), include_index=False)


def test_subscription_sync_replaces_brands_and_keeps_category_definitions(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    seed_store(config)
    active = TencentDocSubscription("Active", "https://docs.qq.com/sheet/active")
    disabled = TencentDocSubscription("Disabled", "https://docs.qq.com/sheet/disabled", enabled=False)
    save_subscriptions(config.index_dir / "tencent_doc_subscriptions.json", [active, disabled])
    save_category_catalog({"Sample": ["Example"], "Old": ["Previous"]}, {"Sample": ["OldBrand"], "Old": [TOSHIBA]})
    definitions = category_catalog()[0]
    items = [item_for(active, config, MIDEA), item_for(disabled, config, TOSHIBA)]
    sync_subscription_brand_replies(config, items)
    aliases, brands = category_catalog()
    assert aliases == definitions
    assert brands["Sample"] == [MIDEA]
    assert brands["Old"] == []

    save_subscriptions(config.index_dir / "tencent_doc_subscriptions.json", [])
    sync_subscription_brand_replies(config, items)
    assert category_catalog()[0] == definitions
    assert all(not values for values in category_catalog()[1].values())


def test_disabling_subscription_immediately_clears_its_brand_statistics(tmp_path: Path) -> None:
    from customer_rag.local_task_api import _TaskApiHandler

    config = make_config(tmp_path)
    seed_store(config)
    subscription = TencentDocSubscription("Active", "https://docs.qq.com/sheet/active")
    save_subscriptions(config.index_dir / "tencent_doc_subscriptions.json", [subscription])
    save_category_catalog({"Sample": []}, {})
    CorpusStore(config.index_dir / "corpus.jsonl").replace_all([item_for(subscription, config, MIDEA)])
    sync_subscription_brand_replies(config)
    assert category_catalog()[1]["Sample"] == [MIDEA]

    handler = object.__new__(_TaskApiHandler)
    assert handler._update_subscription_enabled(config, subscription.url, False)["ok"]
    assert category_catalog()[1]["Sample"] == []
    assert TalkRagStore(config.talk_data_dir).load_realtime_config().brand_reply_rules[0].reply_terms == []


def test_deleting_subscription_source_recounts_remaining_brands(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    seed_store(config)
    first = TencentDocSubscription("First", "https://docs.qq.com/sheet/first")
    second = TencentDocSubscription("Second", "https://docs.qq.com/sheet/second")
    save_subscriptions(config.index_dir / "tencent_doc_subscriptions.json", [first, second])
    save_category_catalog({"Sample": []}, {})
    CorpusStore(config.index_dir / "corpus.jsonl").replace_all([
        item_for(first, config, MIDEA), item_for(second, config, TOSHIBA),
    ])
    sync_subscription_brand_replies(config)
    save_subscriptions(config.index_dir / "tencent_doc_subscriptions.json", [second])
    RagPipeline(config).delete_sources({subscription_output_path(first, config.raw_data_dir)}, rebuild_index=False)
    assert category_catalog()[1]["Sample"] == [TOSHIBA]
