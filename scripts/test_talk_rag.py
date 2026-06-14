from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from customer_rag.talk_rag import (
    BrandReplyRule,
    BrandSaleStatusRule,
    RealtimeTalkConfig,
    TalkRagEngine,
    TalkRagStore,
    clean_tags,
)


def main() -> None:
    with TemporaryDirectory() as tmp_dir:
        store = TalkRagStore(Path(tmp_dir) / "talk_rag")
        engine = TalkRagEngine(store)
        answer = engine.ask("今日清单是什么").answer
        now = datetime.now()
        expected = f"@小助理 {now.month}.{now.day}清单"
        assert answer == expected, f"expected {expected!r}, got {answer!r}"
        assert store.load_links()[0].categories == ["其他"]
        assert clean_tags("全部, 所有, 家电, 家具") == ["家电", "家具"]
        print(f"PASS q='今日清单是什么' a='{answer}'")

        tomorrow = now + timedelta(days=1)
        store.save_realtime_config(
            RealtimeTalkConfig(
                open_group_knowledge=(
                    f"{now.month}.{now.day}\n"
                    "源氏木语专场\n"
                    f"{tomorrow.month}.{tomorrow.day}\n"
                    "全友京东专场"
                ),
                sale_status_rules=[
                    BrandSaleStatusRule(id="s1", brand="源氏木语", aliases=[], status="暂时截团"),
                    BrandSaleStatusRule(id="s2", brand="全友", aliases=["全友床"], status="暂时截团"),
                ],
                brand_reply_rules=[
                    BrandReplyRule(id="r1", keyword_type="品牌", keyword="源氏木语", reply_terms=["@小助理 源氏木语清单"]),
                    BrandReplyRule(id="r2", keyword_type="品牌", keyword="全友", reply_terms=["@小助理 全友清单"]),
                ],
                brand_reply_rules_initialized=True,
            )
        )
        assert engine.ask("源氏木语什么时候开团").answer == "源氏木语今晚8点开团。\n@小助理 源氏木语清单\n先看看款式"
        assert engine.ask("全友床什么时候开团").answer == f"全友{tomorrow.month}.{tomorrow.day}开团，耐心等待。\n@小助理 全友清单\n先看看款式"
        print("PASS open group schedule matching")


if __name__ == "__main__":
    main()
