import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from customer_rag.talk_rag import (
    AssetItem,
    BrandReplyRule,
    COMBINED_TALK_TITLE,
    CombinedReplyRule,
    CombinedTalkConfig,
    FIXED_TALK_PAGE_LABELS,
    FixedReplyRule,
    FixedTalkEntry,
    RealtimeTalkConfig,
    TalkRagEngine,
    TalkRagStore,
    fixed_talk_display_title,
    match_combined_talk,
    match_fixed_talk,
)


class UploadedFileStub:
    def __init__(self, name: str, content: bytes):
        self.name = name
        self._content = BytesIO(content)

    def getbuffer(self):
        return self._content.getbuffer()


class FixedTalkTests(unittest.TestCase):
    def test_matches_keywords_and_joins_multiple_assets(self) -> None:
        assets = [
            AssetItem(id="copy", title="领券文案", paths=[], categories=[], description="先领取优惠券"),
            AssetItem(id="image", title="领券图", paths=["data/coupon.png"], categories=[]),
        ]
        entries = [
            FixedTalkEntry(
                title="领券链接",
                triggers=["{keyword}怎么领券"],
                reply_rules=[FixedReplyRule(id="rule", keywords=["美的", "Midea"], asset_ids=["copy", "image"])],
            )
        ]

        result = match_fixed_talk("美的怎么领券", "领券链接", entries, assets)

        self.assertIsNotNone(result)
        self.assertEqual(result.answer, "先领取优惠券\n---\n素材：领券图\nimage: data/coupon.png")

    def test_entry_title_keeps_rules_isolated(self) -> None:
        entries = [
            FixedTalkEntry(
                title="对比图",
                reply_rules=[FixedReplyRule(id="rule", keywords=["床垫"], asset_ids=["asset"])],
            )
        ]
        assets = [AssetItem(id="asset", title="床垫图", paths=["compare.png"], categories=[])]

        self.assertIsNone(match_fixed_talk("床垫", "领券链接", entries, assets))
        self.assertIsNotNone(match_fixed_talk("床垫", "对比图", entries, assets))

    def test_empty_query_returns_all_fixed_entry_replies(self) -> None:
        entries = [
            FixedTalkEntry(
                title="常用话术",
                reply_rules=[FixedReplyRule(id="rule", keywords=["美的"], asset_ids=["copy"])],
            )
        ]
        assets = [
            AssetItem(id="copy", title="品牌文案", paths=[], categories=[], description="美的专属话术"),
            AssetItem(id="general", title="通用文案", paths=[], categories=["常用话术"], description="通用兜底话术"),
            AssetItem(id="other", title="售后文案", paths=[], categories=["售后话术"], description="售后回复"),
        ]

        result = match_fixed_talk("", "常用话术", entries, assets)

        self.assertIsNotNone(result)
        self.assertEqual(result.answer, "美的专属话术\n---\n通用兜底话术")

    def test_platform_keyword_promotes_matching_order_link_prefix(self) -> None:
        entries = [
            FixedTalkEntry(
                title="links",
                reply_rules=[FixedReplyRule(id="rule", keywords=["sofa"], asset_ids=["taobao", "jd", "neutral"])],
            )
        ]
        assets = [
            AssetItem(id="taobao", title="taobao", paths=[], categories=[], description="order https://s.click.taobao.com/abc"),
            AssetItem(id="jd", title="jd", paths=[], categories=[], description="order https://u.jd.com/abc"),
            AssetItem(id="neutral", title="neutral", paths=[], categories=[], description="no order link"),
        ]

        result = match_fixed_talk("\u4eac\u4e1c sofa", "links", entries, assets)

        self.assertIsNotNone(result)
        self.assertEqual([asset.id for asset in result.assets], ["jd", "taobao", "neutral"])

    def test_tmall_keyword_promotes_tmall_before_taobao_short_link(self) -> None:
        entries = [
            FixedTalkEntry(
                title="links",
                reply_rules=[FixedReplyRule(id="rule", keywords=["lamp"], asset_ids=["taobao", "tmall"])],
            )
        ]
        assets = [
            AssetItem(id="taobao", title="taobao", paths=[], categories=[], description="order https://s.click.taobao.com/abc"),
            AssetItem(id="tmall", title="tmall", paths=[], categories=[], description="order https://detail.tmall.com/item.htm?id=1"),
        ]

        result = match_fixed_talk("\u5929\u732b lamp", "links", entries, assets)

        self.assertIsNotNone(result)
        self.assertEqual([asset.id for asset in result.assets], ["tmall", "taobao"])

    def test_combined_talk_expands_mapping_term_to_fixed_keywords(self) -> None:
        config = CombinedTalkConfig(
            triggers=["检索{keyword}"],
            reply_rules=[
                CombinedReplyRule(
                    id="combo",
                    mapping_term="1",
                    keywords=["九牧", "智能马桶"],
                    reply_titles=["常用话术"],
                )
            ],
        )
        entries = [
            FixedTalkEntry(
                title="常用话术",
                reply_rules=[FixedReplyRule(id="rule", keywords=["智能马桶"], asset_ids=["copy"])],
            )
        ]
        assets = [AssetItem(id="copy", title="智能马桶话术", paths=[], categories=[], description="智能马桶推荐话术")]

        result = match_combined_talk("检索1", config, RealtimeTalkConfig(), entries, assets)

        self.assertIsNotNone(result)
        self.assertEqual(result.answer, "智能马桶推荐话术")
        self.assertIn("映射词：1", result.chain)

    def test_engine_combined_talk_entry_uses_saved_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TalkRagStore(Path(temp_dir) / "talk_rag")
            store.ensure_seed_data()
            store.save_assets([AssetItem(id="copy", title="九牧话术", paths=[], categories=[], description="九牧固定回复")])
            store.save_fixed_entries(
                [
                    FixedTalkEntry(
                        title="常用话术",
                        reply_rules=[FixedReplyRule(id="rule", keywords=["九牧"], asset_ids=["copy"])],
                    )
                ]
            )
            store.save_combined_config(
                CombinedTalkConfig(
                    triggers=["检索{keyword}"],
                    reply_rules=[
                        CombinedReplyRule(id="combo", mapping_term="1", keywords=["九牧"], reply_titles=["常用话术"])
                    ],
                )
            )

            result = TalkRagEngine(store).ask("检索1", COMBINED_TALK_TITLE)

            self.assertEqual(result.answer, "九牧固定回复")

    def test_engine_exposes_all_eight_shortcuts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TalkRagStore(Path(temp_dir) / "talk_rag")
            engine = TalkRagEngine(store)

            results = engine.ask_shortcuts("今日清单是什么")

            self.assertEqual(len(results), 8)
            self.assertIn("清单", results[1].answer)

    def test_fixed_aliases_default_to_original_names_and_fallback_to_page_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TalkRagStore(Path(temp_dir) / "talk_rag")

            aliases = store.load_fixed_aliases()
            self.assertEqual(aliases["领券链接"], "领券链接")
            self.assertEqual(fixed_talk_display_title("领券链接", aliases), "领券链接")

            aliases["领券链接"] = ""
            store.save_fixed_aliases(aliases)
            reloaded = store.load_fixed_aliases()

            self.assertEqual(reloaded["领券链接"], "")
            self.assertEqual(fixed_talk_display_title("领券链接", reloaded), FIXED_TALK_PAGE_LABELS[0])

    def test_shortcut_labels_use_fixed_aliases_without_changing_shortcut_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TalkRagStore(Path(temp_dir) / "talk_rag")
            aliases = store.load_fixed_aliases()
            aliases["领券链接"] = "优惠入口"
            aliases["常用话术"] = ""
            store.save_fixed_aliases(aliases)
            engine = TalkRagEngine(store)

            labels = engine.shortcut_labels()

            self.assertEqual(labels[:4], ["组合话术", "实时话术", "优惠入口", "分页2"])

    def test_store_updates_asset_text_and_replaces_uploaded_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TalkRagStore(Path(temp_dir) / "talk_rag")
            asset = store.save_uploaded_assets(
                [UploadedFileStub("old.png", b"old image")],
                "旧素材",
                ["领券链接"],
                "",
            )

            updated_text = store.update_asset(
                asset.id,
                title="新素材",
                categories=["常用话术"],
                description="新的文案",
            )

            self.assertEqual(updated_text.title, "新素材")
            self.assertEqual(updated_text.categories, ["常用话术"])
            self.assertEqual(updated_text.description, "新的文案")
            self.assertEqual(Path(updated_text.paths[0]).read_bytes(), b"old image")

            updated_file = store.update_asset(
                asset.id,
                title="新素材",
                categories=["常用话术"],
                files=[UploadedFileStub("new.png", b"new image")],
            )

            self.assertEqual(Path(updated_file.paths[0]).name, "new.png")
            self.assertEqual(Path(updated_file.paths[0]).read_bytes(), b"new image")
            self.assertFalse((store.asset_dir / asset.id / "old.png").exists())

    def test_store_exports_and_imports_config_zip_with_assets(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = TalkRagStore(Path(source_dir) / "talk_rag")
            source.ensure_seed_data()
            asset_path = source.asset_dir / "asset-1" / "coupon.png"
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_bytes(b"fake image")
            source.save_assets(
                [
                    AssetItem(
                        id="asset-1",
                        title="领券图",
                        paths=[str(asset_path)],
                        categories=["领券链接"],
                        description="领券图片文案",
                    )
                ]
            )
            source.save_fixed_entries(
                [
                    FixedTalkEntry(
                        title="领券链接",
                        triggers=["{keyword}怎么领券"],
                        reply_rules=[FixedReplyRule("rule-1", ["美的"], ["asset-1"])],
                    )
                ]
            )
            source.save_realtime_config(
                RealtimeTalkConfig(
                    brand_reply_rules=[
                        BrandReplyRule("brand-1", "品牌", "美的", ["@小助理 美的清单"], "补充回复")
                    ],
                    brand_reply_rules_initialized=True,
                    open_group_knowledge="美的 6.18 开团",
                )
            )

            target = TalkRagStore(Path(target_dir) / "talk_rag")
            summary = target.import_config_zip(source.export_config_zip())

            self.assertEqual(summary["assets"], 1)
            self.assertEqual(summary["realtime_brand_rules"], 1)
            self.assertEqual(target.load_realtime_config().open_group_knowledge, "美的 6.18 开团")
            imported_entry = next(item for item in target.load_fixed_entries() if item.title == "领券链接")
            self.assertEqual(imported_entry.reply_rules[0].asset_ids, ["asset-1"])
            imported_asset = target.load_assets()[0]
            self.assertEqual(imported_asset.description, "领券图片文案")
            self.assertTrue(Path(imported_asset.paths[0]).is_file())
            self.assertEqual(Path(imported_asset.paths[0]).read_bytes(), b"fake image")

    def test_store_selectively_imports_fixed_module_without_realtime_or_other_modules(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = TalkRagStore(Path(source_dir) / "talk_rag")
            source.ensure_seed_data()
            source_asset_path = source.asset_dir / "coupon-asset" / "coupon.png"
            source_asset_path.parent.mkdir(parents=True, exist_ok=True)
            source_asset_path.write_bytes(b"source coupon")
            source.save_assets(
                [
                    AssetItem(
                        id="coupon-asset",
                        title="新领券图",
                        paths=[str(source_asset_path)],
                        categories=["领券链接"],
                    ),
                    AssetItem(
                        id="compare-asset",
                        title="源对比图",
                        paths=[],
                        categories=["对比图"],
                    ),
                ]
            )
            source.save_fixed_entries(
                [
                    FixedTalkEntry(
                        title="领券链接",
                        reply_rules=[FixedReplyRule("source-coupon", ["新领券"], ["coupon-asset"])],
                    ),
                    FixedTalkEntry(
                        title="对比图",
                        reply_rules=[FixedReplyRule("source-compare", ["新对比"], ["compare-asset"])],
                    ),
                ]
            )
            source.save_realtime_config(
                RealtimeTalkConfig(
                    brand_reply_rules=[BrandReplyRule("source-brand", "品牌", "源品牌", ["源回复"])],
                    brand_reply_rules_initialized=True,
                    open_group_knowledge="源开团",
                )
            )

            target = TalkRagStore(Path(target_dir) / "talk_rag")
            target.ensure_seed_data()
            target_asset_path = target.asset_dir / "old-coupon" / "old.png"
            target_asset_path.parent.mkdir(parents=True, exist_ok=True)
            target_asset_path.write_bytes(b"old coupon")
            target.save_assets(
                [
                    AssetItem(
                        id="old-coupon",
                        title="旧领券图",
                        paths=[str(target_asset_path)],
                        categories=["领券链接"],
                    ),
                    AssetItem(
                        id="target-compare",
                        title="目标对比图",
                        paths=[],
                        categories=["对比图"],
                    ),
                ]
            )
            target.save_fixed_entries(
                [
                    FixedTalkEntry(
                        title="领券链接",
                        reply_rules=[FixedReplyRule("target-coupon", ["旧领券"], ["old-coupon"])],
                    ),
                    FixedTalkEntry(
                        title="对比图",
                        reply_rules=[FixedReplyRule("target-compare", ["旧对比"], ["target-compare"])],
                    ),
                ]
            )
            target.save_realtime_config(
                RealtimeTalkConfig(
                    brand_reply_rules=[BrandReplyRule("target-brand", "品牌", "目标品牌", ["目标回复"])],
                    brand_reply_rules_initialized=True,
                    open_group_knowledge="目标开团",
                )
            )

            package = source.export_config_zip(include_realtime=False, fixed_titles=["领券链接"])
            target.import_config_zip(package, include_realtime=False, fixed_titles=["领券链接"])

            self.assertEqual(target.load_realtime_config().open_group_knowledge, "目标开团")
            entries = {entry.title: entry for entry in target.load_fixed_entries()}
            self.assertEqual(entries["领券链接"].reply_rules[0].keywords, ["新领券"])
            self.assertEqual(entries["对比图"].reply_rules[0].keywords, ["旧对比"])
            assets = {asset.id: asset for asset in target.load_assets()}
            self.assertIn("coupon-asset", assets)
            self.assertIn("target-compare", assets)
            self.assertNotIn("old-coupon", assets)
            self.assertEqual(Path(assets["coupon-asset"].paths[0]).read_bytes(), b"source coupon")


if __name__ == "__main__":
    unittest.main()
