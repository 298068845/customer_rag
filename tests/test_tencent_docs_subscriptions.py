from customer_rag.tencent_docs import TencentDocSubscription, merge_reimported_subscriptions


def test_merge_reimported_subscriptions_updates_same_name_changed_url() -> None:
    existing = [
        TencentDocSubscription(
            name="乐至宝",
            url="https://docs.qq.com/sheet/old",
            tags=["旧"],
            last_status="跳过：文件未变化",
            last_modified="2026-06-15 10:00:00",
        )
    ]
    imported = [
        TencentDocSubscription(
            name="乐至宝",
            url="https://docs.qq.com/sheet/new",
            tags=["新"],
        )
    ]

    merged, stats = merge_reimported_subscriptions(existing, imported)

    assert stats == {"added": 0, "updated": 1, "skipped": 0}
    assert len(merged) == 1
    assert merged[0].url == "https://docs.qq.com/sheet/new"
    assert merged[0].tags == ["新"]
    assert merged[0].last_status == ""
    assert merged[0].last_modified == ""


def test_merge_reimported_subscriptions_skips_same_url() -> None:
    existing = [TencentDocSubscription(name="乐至宝", url="https://docs.qq.com/sheet/same")]
    imported = [TencentDocSubscription(name="乐至宝新版", url="https://docs.qq.com/sheet/same")]

    merged, stats = merge_reimported_subscriptions(existing, imported)

    assert stats == {"added": 0, "updated": 0, "skipped": 1}
    assert merged == existing
