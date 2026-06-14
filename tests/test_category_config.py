from __future__ import annotations

import yaml

from customer_rag.category_config import add_category_terms, category_catalog


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
