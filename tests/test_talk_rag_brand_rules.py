import unittest
from unittest.mock import patch

from customer_rag.category_config import category_terms
from customer_rag.talk_rag import (
    BrandReplyRule,
    RealtimeTalkConfig,
    TalkRagEngine,
    default_realtime_config,
    deduplicate_reply_parts,
    extract_category_from_question,
    match_realtime_talk,
    realtime_config_from_payload,
    render_brand_reply,
    render_keyword_reply,
)


class BrandReplyRuleTests(unittest.TestCase):
    def test_current_reply_rules_are_built_in_defaults(self) -> None:
        config = default_realtime_config()

        self.assertTrue(config.brand_reply_rules_initialized)
        self.assertEqual(len(config.brand_reply_rules), 164)
        self.assertTrue(any(rule.keyword == "CCF地毯" and rule.reply_terms for rule in config.brand_reply_rules))

    @patch("customer_rag.category_config.category_aliases", return_value={})
    def test_standard_rag_expands_smart_lock_semantics_without_yaml(self, _category_aliases) -> None:
        expected = {"智能门锁", "智能锁", "门锁", "指纹锁"}
        for question in ("咨询智能门锁", "咨询智能锁", "咨询门锁", "有指纹锁吗"):
            with self.subTest(question=question):
                self.assertTrue(expected.issubset(category_terms(question)))

    @patch(
        "customer_rag.talk_rag.category_aliases",
        return_value={"智能门锁": []},
    )
    @patch("customer_rag.talk_rag._indexed_category_brands", return_value={})
    def test_smart_lock_category_recognizes_common_names_without_yaml_aliases(
        self, _indexed_categories, _category_aliases
    ) -> None:
        for question in ("咨询智能锁", "有门锁吗", "指纹锁还有卖吗"):
            with self.subTest(question=question):
                self.assertEqual(extract_category_from_question(question), "智能门锁")

    @patch(
        "customer_rag.talk_rag.category_aliases",
        return_value={"指纹锁": []},
    )
    @patch("customer_rag.talk_rag._indexed_category_brands", return_value={})
    def test_smart_lock_semantics_resolve_to_current_catalog_key(self, _indexed_categories, _category_aliases) -> None:
        self.assertEqual(extract_category_from_question("咨询智能门锁"), "指纹锁")

    @patch("customer_rag.talk_rag.category_aliases", return_value={})
    @patch("customer_rag.talk_rag.category_brands", return_value={})
    @patch("customer_rag.talk_rag._indexed_category_brands", return_value={"智能门锁": ["德施曼"]})
    def test_smart_lock_category_can_come_directly_from_current_index(
        self, _indexed_categories, _category_brands, _category_aliases
    ) -> None:
        config = RealtimeTalkConfig(
            brand_triggers=["{keyword}"],
            brand_reply_rules=[
                BrandReplyRule(id="lock", keyword_type="品牌", keyword="德施曼", reply_terms=["@小助理 德施曼清单"])
            ],
        )

        result = match_realtime_talk("咨询门锁", config)

        self.assertIsNotNone(result)
        self.assertEqual(result.answer, "@小助理 德施曼清单")

    def test_render_brand_reply_prefers_saved_rule(self) -> None:
        config = RealtimeTalkConfig(
            brand_reply_rules=[
                BrandReplyRule(
                    id="midea",
                    keyword_type="品牌",
                    keyword="美的",
                    reply_terms=["@小助理 美的专属清单", "请查收"],
                )
            ]
        )

        self.assertEqual(render_brand_reply("美的", config), "@小助理 美的专属清单\n请查收")
        self.assertEqual(render_brand_reply("东芝", config), "")

    def test_render_brand_reply_appends_supplemental_reply(self) -> None:
        config = RealtimeTalkConfig(
            brand_reply_rules=[
                BrandReplyRule(
                    id="midea",
                    keyword_type="品牌",
                    keyword="美的",
                    reply_terms=["@小助理 美的清单"],
                    supplemental_reply="这是补充回复\n可以包含多行",
                )
            ]
        )

        self.assertEqual(
            render_brand_reply("美的", config),
            "@小助理 美的清单\n---\n这是补充回复\n可以包含多行",
        )

    @patch("customer_rag.talk_rag.category_brands", return_value={"电饭煲": ["美的", "东芝"]})
    @patch("customer_rag.talk_rag.extract_category_from_question", return_value="电饭煲")
    @patch("customer_rag.talk_rag._indexed_category_brands", return_value={})
    def test_category_reply_uses_each_brand_saved_rule(
        self, _indexed_categories, _extract_category, _category_brands
    ) -> None:
        config = RealtimeTalkConfig(
            brand_reply_rules=[
                BrandReplyRule(
                    id="midea",
                    keyword_type="品牌",
                    keyword="美的",
                    reply_terms=["美的自定义回复"],
                )
            ]
        )

        self.assertEqual(render_keyword_reply("有电饭煲吗", config), "美的自定义回复")

    def test_duplicate_category_replies_are_removed_by_message(self) -> None:
        self.assertEqual(
            deduplicate_reply_parts(
                [
                    "@小助理 追觅清单",
                    "@小助理 扫地机洗地机擦窗机清单\n---\n补充说明",
                    "@小助理 扫地机洗地机擦窗机清单\n---\n补充说明",
                ]
            ),
            "@小助理 追觅清单\n---\n@小助理 扫地机洗地机擦窗机清单\n---\n补充说明",
        )

    def test_empty_reply_ignores_brand(self) -> None:
        config = RealtimeTalkConfig(
            brand_triggers=["{keyword}"],
            open_group_triggers=["{brand}什么时候播"],
            brand_reply_rules=[BrandReplyRule(id="empty", keyword_type="品牌", keyword="空品牌", reply_terms=[])],
        )

        self.assertIsNone(match_realtime_talk("空品牌", config))
        self.assertIsNone(match_realtime_talk("空品牌什么时候播", config))

    def test_existing_brand_rules_mark_list_initialized(self) -> None:
        config = realtime_config_from_payload(
            {
                "brand_reply_rules": [
                    {"id": "saved", "keyword_type": "品牌", "keyword": "已保存", "reply_terms": ["回复"]}
                ]
            }
        )

        self.assertTrue(config.brand_reply_rules_initialized)

    @patch("customer_rag.talk_rag.category_aliases", return_value={})
    @patch("customer_rag.talk_rag.category_brands", return_value={"电饭煲": ["美的"]})
    def test_unknown_keyword_is_not_treated_as_brand(self, _category_brands, _category_aliases) -> None:
        config = RealtimeTalkConfig(brand_triggers=["{keyword}"])

        self.assertIsNone(match_realtime_talk("路由器", config))

    @patch("customer_rag.talk_rag.category_aliases", return_value={})
    @patch("customer_rag.talk_rag.category_brands", return_value={})
    def test_saved_brand_rule_is_a_known_brand(self, _category_brands, _category_aliases) -> None:
        config = RealtimeTalkConfig(
            brand_triggers=["{keyword}"],
            brand_reply_rules=[
                BrandReplyRule(id="custom", keyword_type="品牌", keyword="测试品牌", reply_terms=["自定义回复"])
            ],
        )

        result = match_realtime_talk("测试品牌", config)

        self.assertIsNotNone(result)
        self.assertEqual(result.answer, "自定义回复")

    @patch("customer_rag.talk_rag.TalkRagStore.load_realtime_config", return_value=RealtimeTalkConfig())
    @patch("customer_rag.talk_rag.category_aliases", return_value={})
    @patch("customer_rag.talk_rag.category_brands", return_value={})
    def test_engine_uses_expected_fallback(self, _category_brands, _category_aliases, _load_config) -> None:
        result = TalkRagEngine().ask("路由器")

        self.assertEqual(result.answer, "没有做这个的，看看别的渠道")


if __name__ == "__main__":
    unittest.main()
