from __future__ import annotations

from customer_rag import pipeline


class _FakeCategoryMatch:
    def group(self, index: int) -> str:
        assert index == 1
        return "kitchen-appliance"


def test_extract_category_fields_expands_compound_category(monkeypatch):
    monkeypatch.setattr(pipeline.re, "finditer", lambda *_args, **_kwargs: [_FakeCategoryMatch()])

    assert pipeline._extract_category_fields("unused") == [
        "kitchen-appliance",
        "kitchen",
        "appliance",
    ]


def test_category_brands_for_query_uses_category_aliases(monkeypatch):
    monkeypatch.setattr(pipeline, "_category_terms", lambda _query: ["coffee maker"])
    monkeypatch.setattr(pipeline, "category_aliases", lambda: {"Coffee Machine": ["coffee maker"]})
    monkeypatch.setattr(pipeline, "category_brands", lambda: {"Coffee Machine": ["Brand A"]})

    assert pipeline._category_brands_for_query("coffee maker") == ["Brand A"]
