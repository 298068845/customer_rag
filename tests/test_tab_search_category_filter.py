from __future__ import annotations

from pathlib import Path

import yaml

from customer_rag.config import RagConfig
from customer_rag.pipeline import RagPipeline
from customer_rag.corpus import CorpusItem
from customer_rag.vector_store import RetrievedChunk
from customer_rag.pipeline import _filter_product_categories
import pytest


def make_config(root: Path) -> RagConfig:
    return RagConfig(
        raw_data_dir=root / "raw",
        index_dir=root / "index",
        embedding_model_path=root / "embedding",
        llm_model_path=root / "model.gguf",
    )


def test_tab_search_drops_uncategorized_notes_and_keeps_brand_products(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "category_aliases.yaml").write_text(
        yaml.safe_dump(
            {
                "categories": {
                    "电视": {"aliases": [], "brands": ["海信"]},
                    "海信": {"aliases": ["海信-天猫"], "brands": []},
                }
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    pipeline = RagPipeline(make_config(tmp_path), require_categorized_results=True)
    pipeline.add_corpus(
        title="海信活动说明",
        text="品牌: 海信；产品信息: 档期活动说明；下单流程: 仅限京东平台",
    )
    pipeline.add_corpus(
        title="海信电视 X100",
        text="品牌: 海信；品类: 电视；产品信息: 海信电视 X100；商品链接: https://u.jd.com/hisense",
    )

    results = pipeline.keyword_search("海信", top_k=10)

    assert [result.title for result in results] == ["海信电视 X100"]


@pytest.mark.parametrize("tab_flow", [False, True])
def test_microwave_title_remains_searchable_under_broad_category(tmp_path, monkeypatch, tab_flow):
    monkeypatch.chdir(tmp_path)
    from customer_rag.category_config import save_category_catalog
    save_category_catalog({"微波炉": [], "小家电": [], "净水器": []}, {"小家电": ["海尔"]})
    pipeline = RagPipeline(make_config(tmp_path), require_categorized_results=tab_flow)
    pipeline.corpus.replace_all([
        CorpusItem(
            "microwave", "海尔白巧变频微波炉",
            "品牌: 海尔；品类: 小家电；产品信息: HW-M2001YW；商品链接: 待定",
            "manual", "row 1", "", "", tags=["海尔", "小家电"],
        ),
        CorpusItem(
            "purifier", "海尔净水器",
            "品牌: 海尔；品类: 净水器；产品信息: 净水器；其他说明: 微波炉活动推荐",
            "manual", "row 2", "", "", tags=["海尔", "净水器"],
        ),
    ])
    assert [s.title for s in pipeline.keyword_search("微波炉", 10)] == ["海尔白巧变频微波炉"]
    result = pipeline.ask("微波炉")
    assert [s.title for s in result.sources] == ["海尔白巧变频微波炉"]
    assert "HW-M2001YW" in result.answer
    assert not result.fallback
    assert not result.timed_out


def test_category_rescue_requires_product_evidence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from customer_rag.category_config import save_category_catalog
    save_category_catalog({"微波炉": [], "小家电": []}, {})
    sources = [
        RetrievedChunk("品类: 小家电；型号/规格: 变频微波炉", "manual", "HW-M2001YW", "row 1", 100),
        RetrievedChunk("品类: 小家电；产品信息: 咖啡机；其他说明: 微波炉活动", "微波炉.xlsx", "咖啡机", "微波炉 row 2", 100),
    ]
    assert _filter_product_categories("微波炉", sources) == [sources[0]]


def test_fast_category_path_includes_title_hits_under_broad_category(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from customer_rag.category_config import save_category_catalog
    save_category_catalog({"微波炉": [], "小家电": []}, {})
    pipeline = RagPipeline(make_config(tmp_path), require_categorized_results=True)
    items = [
        CorpusItem(str(i), f"其他品牌微波炉 {i}", "品牌: 其他；品类: 微波炉；产品信息: 微波炉",
                   "manual", str(i), "", "", tags=["微波炉"])
        for i in range(3)
    ]
    items.append(CorpusItem("haier", "海尔白巧变频微波炉", "品牌: 海尔；品类: 小家电；产品信息: HW-M2001YW",
                            "manual", "haier", "", "", tags=["海尔", "小家电"]))
    pipeline.corpus.replace_all(items)
    assert "海尔白巧变频微波炉" in [s.title for s in pipeline.category_search("微波炉", 10)]
    assert "海尔白巧变频微波炉" in [s.title for s in pipeline.ask("微波炉").sources]


def test_catalog_brand_list_does_not_drop_real_matching_brand(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from customer_rag.category_config import save_category_catalog
    save_category_catalog({"微波炉": []}, {"微波炉": ["已登记品牌"]})
    pipeline = RagPipeline(make_config(tmp_path))
    pipeline.corpus.replace_all([
        CorpusItem(brand, f"{brand}微波炉", f"品牌: {brand}；品类: 微波炉；产品信息: 微波炉",
                   "manual", brand, "", "", tags=["微波炉"])
        for brand in ["已登记品牌", "海尔"]
    ])
    assert {s.title for s in pipeline.ask("微波炉").sources} == {"已登记品牌微波炉", "海尔微波炉"}


def test_specific_query_in_parent_bucket_rejects_sibling_products(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from customer_rag.category_config import save_category_catalog
    save_category_catalog({"小家电": ["咖啡机", "小家电-咖啡机", "空气炸锅", "小家电-空气炸锅"]}, {})
    pipeline = RagPipeline(make_config(tmp_path), require_categorized_results=True)
    pipeline.corpus.replace_all([
        CorpusItem(kind, f"海尔{kind}", f"品牌: 海尔；品类: 小家电；产品信息: {kind}",
                   "manual", kind, "", "", tags=["小家电"])
        for kind in ["咖啡机", "剃须刀", "空气炸锅"]
    ])
    for kind in ["咖啡机", "剃须刀", "空气炸锅"]:
        assert [s.title for s in pipeline.keyword_search(kind, 10)] == [f"海尔{kind}"]
        assert [s.title for s in pipeline.ask(kind).sources] == [f"海尔{kind}"]


def test_tab_does_not_treat_categorized_sheet_note_as_product(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from customer_rag.category_config import save_category_catalog
    save_category_catalog({"咖啡机": []}, {})
    pipeline = RagPipeline(make_config(tmp_path), require_categorized_results=True)
    pipeline.corpus.replace_all([
        CorpusItem("note", "海信.xlsx / 工作表1", "品牌: 古洛尼；品类: 咖啡机；其他说明: 套购优惠；商品链接: https://example.com",
                   "manual", "1", "", "", tags=["咖啡机"]),
        CorpusItem("product", "古洛尼咖啡机", "品牌: 古洛尼；品类: 咖啡机；产品信息: EP91",
                   "manual", "2", "", "", tags=["咖啡机"]),
    ])
    assert [s.title for s in pipeline.ask("咖啡机").sources] == ["古洛尼咖啡机"]


def test_title_index_refreshes_when_category_catalog_changes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from customer_rag.category_config import save_category_catalog
    save_category_catalog({"咖啡机": []}, {})
    pipeline = RagPipeline(make_config(tmp_path))
    pipeline.corpus.replace_all([
        CorpusItem("new", "海尔果蔬机", "品牌: 海尔；品类: 小家电；产品信息: GS100",
                   "manual", "1", "", "", tags=["小家电"]),
    ])
    pipeline._corpus_items()
    save_category_catalog({"咖啡机": [], "果蔬机": []}, {})
    assert [s.title for s in pipeline.category_search("果蔬机", 10)] == ["海尔果蔬机"]
    assert [s.title for s in RagPipeline(pipeline.config).category_search("果蔬机", 10)] == ["海尔果蔬机"]


def test_polluted_water_catalog_cannot_recall_stove_sets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from customer_rag.category_config import save_category_catalog
    save_category_catalog({"净水": ["净水-厨下", "套装", "烟灶", "烟灶-套装"]}, {})
    pipeline = RagPipeline(make_config(tmp_path), require_categorized_results=True)
    pipeline.corpus.replace_all([
        CorpusItem("water", "海尔净水器", "品牌: 海尔；品类: 净水-厨下；产品信息: 净水器",
                   "manual", "water", "", "", tags=["净水"]),
        CorpusItem("stove", "海尔烟灶套装", "品牌: 海尔；品类: 烟灶-套装；产品信息: 烟灶套装",
                   "manual", "stove", "", "", tags=["烟灶"]),
    ])
    assert [s.title for s in pipeline.ask("净水器").sources] == ["海尔净水器"]
    assert [s.title for s in pipeline.ask("烟灶").sources] == ["海尔烟灶套装"]


def test_parent_category_narrowing_keeps_real_shaver_synonym(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from customer_rag.category_config import save_category_catalog
    save_category_catalog({"小家电": ["剃须刀", "刮胡刀", "咖啡机", "小家电-咖啡机"]}, {})
    pipeline = RagPipeline(make_config(tmp_path), require_categorized_results=True)
    pipeline.corpus.replace_all([
        CorpusItem("shaver", "海尔刮胡刀", "品牌: 海尔；品类: 小家电；产品信息: SH100",
                   "manual", "1", "", "", tags=["小家电"]),
    ])
    assert [s.title for s in pipeline.ask("剃须刀").sources] == ["海尔刮胡刀"]
