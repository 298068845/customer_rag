from __future__ import annotations

import yaml

from customer_rag.category_config import add_category_terms, category_catalog, category_terms, replace_category_brands, save_category_catalog


def test_parent_bucket_does_not_expand_specific_query_to_sibling_products(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_category_catalog({
        "微波炉": ["厨房电器", "厨房电器-微波炉", "烤箱", "厨房电器-烤箱"],
        "小家电": ["咖啡机", "小家电-咖啡机", "空气炸锅", "小家电-空气炸锅"],
    })
    microwave = category_terms("微波炉")
    assert "微波炉" in microwave
    assert "烤箱" not in microwave and "厨房电器" not in microwave
    shaver = category_terms("剃须刀")
    assert "剃须刀" in shaver
    assert "咖啡机" not in shaver and "小家电" not in shaver
    coffee = category_terms("咖啡机")
    assert "咖啡机" in coffee
    assert "空气炸锅" not in coffee


def test_import_does_not_assign_sibling_to_shared_parent_alias(tmp_path):
    path = tmp_path / "category_aliases.yaml"
    save_category_catalog({"微波炉": ["厨房电器"], "小家电": []}, {}, path)
    add_category_terms(["厨房电器-烤箱", "小家电-咖啡机"], path)
    aliases, _ = category_catalog(path)
    assert "烤箱" in aliases and "咖啡机" in aliases
    assert "厨房电器-烤箱" not in aliases["微波炉"]


def test_category_catalog_merges_compound_air_conditioner_categories(tmp_path):
    path = tmp_path / "category_aliases.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "categories": {
                    "空调-挂1.5P": {"aliases": ["挂1.5P"], "brands": ["海尔", "卡萨帝"]},
                    "空调-柜3P": {"aliases": ["柜3P"], "brands": ["卡萨帝", "海尔"]},
                    "空调-柜机3匹": {"aliases": ["柜机3匹"], "brands": ["美的"]},
                }
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    aliases, brands = category_catalog(path)

    assert "空调" in aliases
    assert "空调-挂1.5P" not in aliases
    assert "空调-柜3P" not in aliases
    assert {"挂1.5P", "柜3P", "柜机3匹"}.issubset(set(aliases["空调"]))
    assert brands["空调"] == ["海尔", "卡萨帝", "美的"]


def test_add_category_terms_normalizes_category_and_brand_synonyms(tmp_path):
    path = tmp_path / "category_aliases.yaml"
    path.write_text(
        yaml.safe_dump({"categories": {}}, allow_unicode=True),
        encoding="utf-8",
    )

    add_category_terms(
        ["空调-柜3P", "厨房电器-电饭煲"],
        path,
        category_brand_map={
            "空调-柜3P": ["海信-天猫", "天猫-松下", "海信"],
            "厨房电器-电饭煲": ["美的"],
        },
    )
    aliases, brands = category_catalog(path)

    assert "空调" in aliases
    assert "柜3P" in aliases["空调"]
    assert brands["空调"] == ["海信", "松下"]
    assert "电饭煲" in aliases
    assert "厨房电器-电饭煲" in aliases["电饭煲"]
    assert brands["电饭煲"] == ["美的"]


def test_replace_brands_keeps_categories_and_aliases_and_clears_old_brands(tmp_path):
    path = tmp_path / "categories.yaml"
    save_category_catalog(
        {"净水器": ["厨下净水器"], "照明": ["灯具"], "停售类目": ["旧同义词"]},
        {"净水器": ["旧品牌"], "照明": ["旧品牌"], "停售类目": ["旧品牌"]},
        path,
    )
    aliases = category_catalog(path)[0]
    replace_category_brands({"厨下净水器": ["美的-天猫", "美的"], "照明-灯具": ["雷士"], "未知类目": ["未知品牌"]}, path)
    current_aliases, brands = category_catalog(path)
    assert current_aliases == aliases
    assert brands["净水器"] == ["美的"]
    assert brands["照明"] == ["雷士"]
    assert brands["停售类目"] == []
    signature = path.stat().st_mtime_ns
    assert replace_category_brands({"厨下净水器": ["美的"], "照明": ["雷士"]}, path) == 0
    assert path.stat().st_mtime_ns == signature


def test_catalog_ignores_brand_only_pseudo_categories(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "category_aliases.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "categories": {
                    "电视": {"aliases": ["MiniLED"], "brands": ["海信"]},
                    "海信": {"aliases": ["海信-天猫"], "brands": []},
                }
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    aliases, brands = category_catalog(path)

    assert "海信" not in aliases
    assert brands["电视"] == ["海信"]
    assert category_terms("海信") == []


def test_add_category_terms_does_not_restore_brand_only_category(tmp_path):
    path = tmp_path / "category_aliases.yaml"
    path.write_text(yaml.safe_dump({"categories": {}}, allow_unicode=True), encoding="utf-8")

    add_category_terms(
        ["海信-天猫"],
        path,
        category_brand_map={"电视": ["海信"]},
    )
    aliases, brands = category_catalog(path)

    assert "海信" not in aliases
    assert brands["电视"] == ["海信"]
