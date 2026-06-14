import tempfile
import unittest
from pathlib import Path

from customer_rag.talk_rag import (
    AssetItem,
    BrandReplyRule,
    FixedReplyRule,
    FixedTalkEntry,
    RealtimeTalkConfig,
    TalkRagEngine,
    TalkRagStore,
    match_fixed_talk,
)


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

    def test_engine_exposes_all_eight_shortcuts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TalkRagStore(Path(temp_dir) / "talk_rag")
            engine = TalkRagEngine(store)

            results = engine.ask_shortcuts("今日清单是什么")

            self.assertEqual(len(results), 8)
            self.assertIn("清单", results[0].answer)

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
