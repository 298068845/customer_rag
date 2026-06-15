import unittest

from talk_app import (
    apply_data_editor_changes,
    apply_data_editor_changes_preserve_values,
    attach_asset_ids_to_rows,
    asset_list_dataframe,
    assets_for_title,
    fixed_reply_rules_dataframe,
    normalize_selected_asset_titles,
    prefer_rows_with_reply_assets,
    referenced_asset_titles,
    rows_to_fixed_reply_rules,
    validate_fixed_reply_asset_bindings,
    validate_persisted_fixed_reply_rules,
)
from customer_rag.talk_rag import AssetItem, FixedReplyRule, FixedTalkEntry


class TalkAppEditorTests(unittest.TestCase):
    def test_apply_data_editor_changes_merges_edit_add_and_delete(self) -> None:
        rows = [
            {"品牌": "品牌A", "回复内容": "旧回复", "补充回复": ""},
            {"品牌": "品牌B", "回复内容": "待删除", "补充回复": ""},
        ]
        state = {
            "edited_rows": {0: {"回复内容": "新回复", "补充回复": "补充"}},
            "deleted_rows": [1],
            "added_rows": [{"品牌": "品牌C", "回复内容": "新增回复", "补充回复": ""}],
        }

        self.assertEqual(
            apply_data_editor_changes(rows, state),
            [
                {"品牌": "品牌A", "回复内容": "新回复", "补充回复": "补充"},
                {"品牌": "品牌C", "回复内容": "新增回复", "补充回复": ""},
            ],
        )

    def test_apply_data_editor_changes_preserves_multiselect_values(self) -> None:
        rows = [
            {"删除": False, "关键词": "领券", "回复内容": ["素材A"]},
            {"删除": False, "关键词": "对比", "回复内容": ["素材B"]},
        ]
        state = {
            "edited_rows": {0: {"回复内容": ["素材A", "素材C"]}, 1: {"删除": True}},
            "added_rows": [{"删除": False, "关键词": "新增", "回复内容": ["素材C"]}],
        }

        self.assertEqual(
            apply_data_editor_changes_preserve_values(rows, state),
            [
                {"删除": False, "关键词": "领券", "回复内容": ["素材A", "素材C"]},
                {"删除": True, "关键词": "对比", "回复内容": ["素材B"]},
                {"删除": False, "关键词": "新增", "回复内容": ["素材C"]},
            ],
        )

    def test_rows_to_fixed_reply_rules_converts_keywords_and_assets(self) -> None:
        rules = rows_to_fixed_reply_rules(
            [{"关键词": "领券, 优惠券", "回复内容": ["素材A", "不存在"]}],
            {"素材A": "asset-a"},
        )

        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].keywords, ["领券", "优惠券"])
        self.assertEqual(rules[0].asset_ids, ["asset-a"])

    def test_rows_to_fixed_reply_rules_accepts_string_asset_selection(self) -> None:
        rules = rows_to_fixed_reply_rules(
            [{"关键词": "智能马桶", "回复内容": "618档期 智能马桶选购指南"}],
            {"618档期 智能马桶选购指南": "asset-a"},
        )

        self.assertEqual(rules[0].asset_ids, ["asset-a"])

    def test_normalize_selected_asset_titles_accepts_serialized_list(self) -> None:
        self.assertEqual(
            normalize_selected_asset_titles("['素材A', '素材B']", {"素材A": "a", "素材B": "b"}),
            ["素材A", "素材B"],
        )

    def test_normalize_selected_asset_titles_accepts_option_dicts(self) -> None:
        self.assertEqual(
            normalize_selected_asset_titles(
                [{"value": "素材A"}, {"label": "素材B"}, {"title": "不存在"}],
                {"素材A": "a", "素材B": "b"},
            ),
            ["素材A", "素材B"],
        )

    def test_validate_fixed_reply_asset_bindings_rejects_unmapped_reply_content(self) -> None:
        errors = validate_fixed_reply_asset_bindings(
            [{"关键词": "九牧", "回复内容": ["九牧优惠券"]}],
            {"恒洁优惠券": "asset-hengjie"},
        )

        self.assertEqual(len(errors), 1)
        self.assertIn("九牧", errors[0])
        self.assertIn("九牧优惠券", errors[0])

    def test_validate_fixed_reply_asset_bindings_accepts_empty_reply_content(self) -> None:
        self.assertEqual(
            validate_fixed_reply_asset_bindings(
                [{"关键词": "九牧", "回复内容": []}],
                {"九牧优惠券": "asset-jiumu"},
            ),
            [],
        )

    def test_validate_persisted_fixed_reply_rules_detects_asset_mismatch(self) -> None:
        errors = validate_persisted_fixed_reply_rules(
            [FixedReplyRule("expected", ["九牧"], ["asset-jiumu"])],
            [FixedReplyRule("persisted", ["九牧"], [])],
        )

        self.assertEqual(len(errors), 1)
        self.assertIn("保存后素材绑定不一致", errors[0])

    def test_attach_asset_ids_to_rows_uses_stored_row_order(self) -> None:
        self.assertEqual(
            attach_asset_ids_to_rows(
                [{"删除": True, "素材名称": "素材A"}],
                [{"删除": False, "素材名称": "素材A", "素材ID": "asset-a"}],
            ),
            [{"删除": True, "素材名称": "素材A", "素材ID": "asset-a"}],
        )

    def test_referenced_asset_titles_blocks_bound_assets(self) -> None:
        self.assertEqual(
            referenced_asset_titles(
                {"asset-a", "asset-b"},
                [FixedTalkEntry(title="领券链接", reply_rules=[FixedReplyRule("rule", ["智能马桶"], ["asset-a"])])],
                [
                    AssetItem(id="asset-a", title="素材A", paths=[], categories=[]),
                    AssetItem(id="asset-b", title="素材B", paths=[], categories=[]),
                ],
            ),
            ["素材A"],
        )

    def test_assets_for_title_filters_each_fixed_module(self) -> None:
        assets = [
            AssetItem(id="coupon", title="领券素材", paths=[], categories=["领券链接"]),
            AssetItem(id="compare", title="对比素材", paths=[], categories=["对比图"]),
        ]

        self.assertEqual([asset.id for asset in assets_for_title(assets, "领券链接")], ["coupon"])
        self.assertEqual([asset.id for asset in assets_for_title(assets, "对比图")], ["compare"])

    def test_prefer_rows_with_reply_assets_keeps_visible_multiselect_value(self) -> None:
        self.assertEqual(
            prefer_rows_with_reply_assets(
                [{"删除": False, "关键词": "泡沫液，九牧", "回复内容": ["九牧赠品"]}],
                [{"删除": False, "关键词": "泡沫液，九牧", "回复内容": []}],
            ),
            [{"删除": False, "关键词": "泡沫液，九牧", "回复内容": ["九牧赠品"]}],
        )

    def test_fixed_reply_rules_dataframe_uses_checkbox_compatible_bool_column(self) -> None:
        dataframe = fixed_reply_rules_dataframe([{"关键词": "恒洁", "回复内容": ["恒洁素材"]}, {}])

        self.assertEqual(str(dataframe["删除"].dtype), "bool")
        self.assertEqual(dataframe["删除"].tolist(), [False, False])
        self.assertEqual(dataframe["回复内容"].tolist(), [["恒洁素材"], []])

    def test_asset_list_dataframe_uses_checkbox_compatible_bool_column(self) -> None:
        dataframe = asset_list_dataframe([{"素材名称": "恒洁素材"}, {"删除": True, "素材名称": "九牧素材"}])

        self.assertEqual(str(dataframe["删除"].dtype), "bool")
        self.assertEqual(dataframe["删除"].tolist(), [False, True])


if __name__ == "__main__":
    unittest.main()
