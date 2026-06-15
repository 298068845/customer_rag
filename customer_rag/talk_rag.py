from __future__ import annotations

import json
import re
import shutil
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Literal
from uuid import uuid4

from customer_rag.category_config import category_aliases, category_brands, semantic_category_terms
from customer_rag.loaders import brand_tags_from_text, category_tags_from_text


LinkType = Literal["fixed", "knowledge", "image"]

FIXED_TALK_TITLES = ["领券链接", "常用话术", "对比图", "售前话术", "售后话术", "活动规则", "自定义"]


_INDEX_CORPUS_PATH = Path("data/index/corpus.jsonl")
_DEFAULT_BRAND_REPLY_RULES_PATH = Path(__file__).with_name("default_brand_reply_rules.json")
_INDEX_CATALOG_MTIME: float | None = None
_INDEX_CATEGORY_BRANDS: dict[str, list[str]] = {}


@dataclass(frozen=True)
class TalkLink:
    id: str
    title: str
    link_type: LinkType
    triggers: list[str]
    categories: list[str]
    response_template: str = ""
    knowledge_prompt: str = ""
    knowledge_ids: list[str] = field(default_factory=list)
    asset_ids: list[str] = field(default_factory=list)
    enabled: bool = True
    priority: int = 50
    updated_at: str = ""


@dataclass(frozen=True)
class KnowledgeItem:
    id: str
    title: str
    content: str
    categories: list[str]
    updated_at: str = ""


@dataclass(frozen=True)
class AssetItem:
    id: str
    title: str
    paths: list[str]
    categories: list[str]
    description: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class FixedReplyRule:
    id: str
    keywords: list[str]
    asset_ids: list[str]


@dataclass(frozen=True)
class FixedTalkEntry:
    title: str
    triggers: list[str] = field(default_factory=lambda: ["{keyword}"])
    reply_rules: list[FixedReplyRule] = field(default_factory=list)


@dataclass(frozen=True)
class TalkMatch:
    answer: str
    link: TalkLink | None
    chain: list[str]
    score: float
    assets: list[AssetItem] = field(default_factory=list)
    knowledge: list[KnowledgeItem] = field(default_factory=list)


@dataclass(frozen=True)
class OpenGroupEntry:
    date_text: str
    content: str


SaleStatus = Literal["售卖中", "暂时截团", "永久截团"]
KeywordType = Literal["品牌", "品类", "品牌别名"]


@dataclass(frozen=True)
class BrandReplyRule:
    id: str
    keyword_type: KeywordType
    keyword: str
    reply_terms: list[str]
    supplemental_reply: str = ""


@dataclass(frozen=True)
class BrandAliasRule:
    id: str
    brand: str
    aliases: list[str]


@dataclass(frozen=True)
class BrandSaleStatusRule:
    id: str
    brand: str
    aliases: list[str]
    status: SaleStatus = "售卖中"


@dataclass(frozen=True)
class RealtimeTalkConfig:
    today_triggers: list[str] = field(default_factory=lambda: ["今日清单是什么", "今天清单是什么", "今日清单", "清单", "今天播什么"])
    today_template: str = "@小助理 {date_m_d}清单"
    brand_triggers: list[str] = field(default_factory=lambda: ["有{keyword}吗", "{keyword}还有卖吗", "有没有{keyword}", "{keyword}清单", "{keyword}什么时候播"])
    brand_reply_rules: list[BrandReplyRule] = field(default_factory=list)
    brand_reply_rules_initialized: bool = False
    brand_alias_rules: list[BrandAliasRule] = field(default_factory=list)
    open_group_triggers: list[str] = field(default_factory=lambda: ["{brand}什么时候开团", "{brand}什么时候播", "{brand}还有卖吗", "{brand}下次是什么时候"])
    sale_status_rules: list[BrandSaleStatusRule] = field(default_factory=list)
    open_group_knowledge: str = ""


class TalkRagStore:
    def __init__(self, root: Path | str = "data/talk_rag"):
        self.root = Path(root)
        self.links_path = self.root / "links.json"
        self.knowledge_path = self.root / "knowledge.json"
        self.assets_path = self.root / "assets.json"
        self.fixed_path = self.root / "fixed.json"
        self.realtime_path = self.root / "realtime.json"
        self.asset_dir = self.root / "assets"

    def ensure_seed_data(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        if not self.links_path.exists():
            self.save_links(default_links())
        if not self.knowledge_path.exists():
            self.save_knowledge(default_knowledge())
        if not self.assets_path.exists():
            self.save_assets([])
        if not self.fixed_path.exists():
            self.save_fixed_entries(default_fixed_entries())
        if not self.realtime_path.exists():
            self.save_realtime_config(default_realtime_config())

    def load_links(self) -> list[TalkLink]:
        self.ensure_seed_data()
        return [TalkLink(**payload) for payload in _read_json_list(self.links_path)]

    def save_links(self, links: list[TalkLink]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _write_json_list(self.links_path, [asdict(link) for link in links])

    def load_knowledge(self) -> list[KnowledgeItem]:
        self.ensure_seed_data()
        return [KnowledgeItem(**payload) for payload in _read_json_list(self.knowledge_path)]

    def save_knowledge(self, items: list[KnowledgeItem]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _write_json_list(self.knowledge_path, [asdict(item) for item in items])

    def load_assets(self) -> list[AssetItem]:
        self.ensure_seed_data()
        return [AssetItem(**payload) for payload in _read_json_list(self.assets_path)]

    def save_assets(self, items: list[AssetItem]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _write_json_list(self.assets_path, [asdict(item) for item in items])

    def load_fixed_entries(self) -> list[FixedTalkEntry]:
        self.ensure_seed_data()
        payloads = _read_json_list(self.fixed_path)
        entries_by_title = {
            str(payload.get("title", "")): fixed_entry_from_payload(payload)
            for payload in payloads
            if isinstance(payload, dict)
        }
        return [entries_by_title.get(title, FixedTalkEntry(title=title)) for title in FIXED_TALK_TITLES]

    def save_fixed_entries(self, entries: list[FixedTalkEntry]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _write_json_list(self.fixed_path, [asdict(entry) for entry in entries])

    def load_realtime_config(self) -> RealtimeTalkConfig:
        self.ensure_seed_data()
        try:
            payload = json.loads(self.realtime_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default_realtime_config()
        return realtime_config_from_payload(payload if isinstance(payload, dict) else {})

    def save_realtime_config(self, config: RealtimeTalkConfig) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.realtime_path.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")

    def save_uploaded_assets(self, files: list, title: str, categories: list[str], description: str = "") -> AssetItem:
        self.ensure_seed_data()
        asset_id = uuid4().hex
        target_dir = self.asset_dir / asset_id
        target_dir.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        for file in files:
            name = Path(getattr(file, "name", "image.png")).name
            target = target_dir / name
            with target.open("wb") as fp:
                fp.write(file.getbuffer())
            paths.append(str(target))
        item = AssetItem(
            id=asset_id,
            title=title.strip() or "未命名素材",
            paths=paths,
            categories=clean_tags(categories),
            description=description.strip(),
            updated_at=now_text(),
        )
        assets = self.load_assets()
        assets.append(item)
        self.save_assets(assets)
        return item

    def delete_asset_files(self, item: AssetItem) -> None:
        for path in item.paths:
            asset_path = Path(path)
            if asset_path.exists():
                try:
                    asset_path.unlink()
                except OSError:
                    pass
        if item.paths:
            parent = Path(item.paths[0]).parent
            if parent.exists() and parent.is_dir() and parent.parent == self.asset_dir:
                shutil.rmtree(parent, ignore_errors=True)

    def export_config_zip(self, include_realtime: bool = True, fixed_titles: list[str] | None = None) -> bytes:
        self.ensure_seed_data()
        selected_fixed_titles = _selected_fixed_titles(fixed_titles)
        selected_assets = _assets_for_fixed_titles(
            self.load_assets(),
            [entry for entry in self.load_fixed_entries() if entry.title in selected_fixed_titles],
            selected_fixed_titles,
        )
        payload = BytesIO()
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "kind": "talk_rag_config",
                        "version": 1,
                        "exported_at": now_text(),
                        "include_realtime": include_realtime,
                        "fixed_titles": selected_fixed_titles,
                        "files": _exported_config_files(include_realtime, selected_fixed_titles),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            if include_realtime and self.realtime_path.exists():
                archive.writestr("realtime.json", self.realtime_path.read_text(encoding="utf-8"))
            if selected_fixed_titles:
                archive.writestr(
                    "fixed.json",
                    json.dumps(
                        [
                            asdict(entry)
                            for entry in self.load_fixed_entries()
                            if entry.title in selected_fixed_titles
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
                archive.writestr(
                    "assets.json",
                    json.dumps([asdict(asset) for asset in selected_assets], ensure_ascii=False, indent=2),
                )

            for asset in selected_assets:
                for index, path_text in enumerate(asset.paths):
                    path = Path(path_text)
                    if not path.is_file():
                        continue
                    archive.write(path, f"assets/{asset.id}/{index}_{path.name}")
        return payload.getvalue()

    def import_config_zip(
        self,
        data: bytes,
        include_realtime: bool = True,
        fixed_titles: list[str] | None = None,
    ) -> dict[str, int]:
        self.ensure_seed_data()
        selected_fixed_titles = _selected_fixed_titles(fixed_titles)
        try:
            archive = zipfile.ZipFile(BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ValueError("请上传有效的 ZIP 配置包。") from exc

        with archive:
            names = set(archive.namelist())
            if include_realtime and "realtime.json" not in names:
                raise ValueError("ZIP 中没有找到实时话术配置。")
            if selected_fixed_titles and "fixed.json" not in names:
                raise ValueError("ZIP 中没有找到固定话术配置。")
            if not include_realtime and not selected_fixed_titles:
                raise ValueError("请至少选择一个导入板块。")
            if "realtime.json" not in names and "fixed.json" not in names and "assets.json" not in names:
                raise ValueError("ZIP 中没有找到话术配置文件。")

            imported_realtime = False
            realtime_payload = _read_zip_json_object(archive, "realtime.json") if include_realtime else None
            if include_realtime and realtime_payload is not None:
                self.save_realtime_config(realtime_config_from_payload(realtime_payload))
                imported_realtime = True

            imported_fixed_titles: list[str] = []
            imported_assets_count = 0
            fixed_payload = _read_zip_json_list(archive, "fixed.json")
            if selected_fixed_titles and fixed_payload is not None:
                entries_by_title = {
                    entry.title: entry
                    for entry in (
                        fixed_entry_from_payload(item)
                        for item in fixed_payload
                        if isinstance(item, dict)
                    )
                    if entry.title
                }
                imported_fixed_titles = [title for title in selected_fixed_titles if title in entries_by_title]
                current_entries = self.load_fixed_entries()
                self.save_fixed_entries([
                    entries_by_title[entry.title]
                    if entry.title in imported_fixed_titles
                    else entry
                    for entry in current_entries
                ])

            assets_payload = _read_zip_json_list(archive, "assets.json")
            if imported_fixed_titles and assets_payload is not None:
                current_assets = self.load_assets()
                kept_assets = [
                    asset for asset in current_assets
                    if not any(title in asset.categories for title in imported_fixed_titles)
                ]
                for asset in current_assets:
                    if asset not in kept_assets:
                        self.delete_asset_files(asset)
                imported_assets: list[AssetItem] = []
                for item in assets_payload:
                    if not isinstance(item, dict):
                        continue
                    if not any(title in clean_tags(item.get("categories", [])) for title in imported_fixed_titles):
                        continue
                    asset = _asset_from_import_payload(item, archive, self.asset_dir)
                    imported_assets.append(asset)
                self.save_assets([*kept_assets, *imported_assets])
                imported_assets_count = len(imported_assets)

        return {
            "imported_realtime": int(imported_realtime),
            "imported_fixed_entries": len(imported_fixed_titles),
            "imported_assets": imported_assets_count,
            "fixed_entries": len(self.load_fixed_entries()),
            "assets": len(self.load_assets()),
            "realtime_brand_rules": len(self.load_realtime_config().brand_reply_rules),
        }


class TalkRagEngine:
    def __init__(self, store: TalkRagStore | None = None):
        self.store = store or TalkRagStore()

    def ask(self, question: str, entry_title: str = "实时话术") -> TalkMatch:
        if entry_title == "实时话术":
            realtime_match = match_realtime_talk(question, self.store.load_realtime_config())
            if realtime_match:
                return realtime_match
        elif entry_title in FIXED_TALK_TITLES:
            fixed_match = match_fixed_talk(
                question,
                entry_title,
                self.store.load_fixed_entries(),
                self.store.load_assets(),
            )
            if fixed_match:
                return fixed_match
        return TalkMatch(
            answer="没有做这个的，看看别的渠道",
            link=None,
            chain=[f"未命中话术入口：{entry_title}", "返回兜底话术"],
            score=0,
        )

    def ask_shortcuts(self, question: str) -> list[TalkMatch]:
        return [self.ask(question, title) for title in ["实时话术", *FIXED_TALK_TITLES]]

    def ask_legacy(self, question: str) -> TalkMatch:
        links = [link for link in self.store.load_links() if link.enabled]
        knowledge = self.store.load_knowledge()
        assets = self.store.load_assets()
        open_group_match = match_open_group_schedule(question, knowledge)
        if open_group_match:
            return open_group_match
        scored = [(score_link(question, link), link) for link in links]
        scored = [(score, link) for score, link in scored if score > 0]
        scored.sort(key=lambda item: (item[0], item[1].priority), reverse=True)
        if not scored:
            return TalkMatch(
                answer="暂时没有命中合适的话术链路。",
                link=None,
                chain=["未命中链路", "返回兜底话术"],
                score=0,
            )

        score, link = scored[0]
        matched_knowledge = select_knowledge(question, link, knowledge)
        matched_assets = select_assets(link, assets)
        answer = render_link_answer(link, matched_knowledge, matched_assets)
        chain = build_chain(question, link, score, matched_knowledge, matched_assets)
        return TalkMatch(
            answer=answer,
            link=link,
            chain=chain,
            score=score,
            assets=matched_assets,
            knowledge=matched_knowledge,
        )


def default_links() -> list[TalkLink]:
    return [
        TalkLink(
            id="default_today_list",
            title="今日清单话术",
            link_type="fixed",
            triggers=["今日清单是什么", "今天清单是什么", "今日清单", "今天清单"],
            categories=["其他"],
            response_template="@小助理 {date_m_d}清单",
            priority=100,
            updated_at=now_text(),
        )
    ]


def default_fixed_entries() -> list[FixedTalkEntry]:
    return [FixedTalkEntry(title=title) for title in FIXED_TALK_TITLES]


def fixed_entry_from_payload(payload: dict) -> FixedTalkEntry:
    return FixedTalkEntry(
        title=str(payload.get("title", "")).strip(),
        triggers=clean_terms(payload.get("triggers", [])) or ["{keyword}"],
        reply_rules=[
            FixedReplyRule(
                id=str(item.get("id") or new_id()),
                keywords=clean_terms(item.get("keywords", [])),
                asset_ids=clean_terms(item.get("asset_ids", [])),
            )
            for item in payload.get("reply_rules", [])
            if isinstance(item, dict)
        ],
    )


def default_knowledge() -> list[KnowledgeItem]:
    return [
        KnowledgeItem(
            id="default_daily_list_note",
            title="每日清单规则",
            content="用户询问今日清单时，按当前日期回复 @小助理 M.D清单。",
            categories=["其他"],
            updated_at=now_text(),
        )
    ]


def default_realtime_config() -> RealtimeTalkConfig:
    brand_reply_rules = _load_default_brand_reply_rules()
    return RealtimeTalkConfig(
        brand_reply_rules=brand_reply_rules,
        brand_reply_rules_initialized=bool(brand_reply_rules),
        open_group_knowledge=_default_open_group_knowledge_from_items(default_knowledge()),
    )


def _load_default_brand_reply_rules() -> list[BrandReplyRule]:
    try:
        payload = json.loads(_DEFAULT_BRAND_REPLY_RULES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return realtime_config_from_payload({"brand_reply_rules": payload}).brand_reply_rules


def realtime_config_from_payload(payload: dict) -> RealtimeTalkConfig:
    return RealtimeTalkConfig(
        today_triggers=clean_terms(payload.get("today_triggers", [])) or RealtimeTalkConfig().today_triggers,
        today_template=str(payload.get("today_template", "") or RealtimeTalkConfig().today_template),
        brand_triggers=clean_terms(payload.get("brand_triggers", [])) or RealtimeTalkConfig().brand_triggers,
        brand_reply_rules=[
            BrandReplyRule(
                id=str(item.get("id") or new_id()),
                keyword_type=_keyword_type(str(item.get("keyword_type", "品牌"))),
                keyword=str(item.get("keyword", "")).strip(),
                reply_terms=clean_terms(item.get("reply_terms", [])),
                supplemental_reply=str(item.get("supplemental_reply", "") or "").strip(),
            )
            for item in payload.get("brand_reply_rules", [])
            if isinstance(item, dict)
        ],
        brand_reply_rules_initialized=bool(
            payload.get("brand_reply_rules_initialized", bool(payload.get("brand_reply_rules")))
        ),
        brand_alias_rules=[
            BrandAliasRule(
                id=str(item.get("id") or new_id()),
                brand=str(item.get("brand", "")).strip(),
                aliases=clean_terms(item.get("aliases", [])),
            )
            for item in payload.get("brand_alias_rules", [])
            if isinstance(item, dict)
        ],
        open_group_triggers=clean_terms(payload.get("open_group_triggers", [])) or RealtimeTalkConfig().open_group_triggers,
        sale_status_rules=[
            BrandSaleStatusRule(
                id=str(item.get("id") or new_id()),
                brand=str(item.get("brand", "")).strip(),
                aliases=clean_terms(item.get("aliases", [])),
                status=_sale_status(str(item.get("status", "售卖中"))),
            )
            for item in payload.get("sale_status_rules", [])
            if isinstance(item, dict)
        ],
        open_group_knowledge=str(payload.get("open_group_knowledge", "") or ""),
    )


def match_realtime_talk(question: str, config: RealtimeTalkConfig) -> TalkMatch | None:
    normalized_question = normalize_text(question)
    if not normalized_question:
        return None
    if any(trigger_matches_question(trigger, question, "keyword") for trigger in config.today_triggers):
        return TalkMatch(
            answer=render_template(config.today_template),
            link=None,
            chain=["命中实时话术：今日清单", f"识别问题：{question}"],
            score=100,
        )

    open_group_brand = extract_brand_from_question(question, config)
    if open_group_brand and not render_brand_reply(open_group_brand, config):
        open_group_brand = ""
    if open_group_brand and any(trigger_matches_question(trigger, question, "brand") for trigger in config.open_group_triggers):
        status_rule = find_sale_status_rule(open_group_brand, config)
        brand = status_rule.brand if status_rule else open_group_brand
        status = status_rule.status if status_rule else "售卖中"
        if status == "售卖中":
            brand_reply = render_brand_reply(brand, config)
            answer = "现在可以买。"
            if brand_reply:
                answer = f"{answer}\n{brand_reply}"
            return TalkMatch(
                answer=answer,
                link=None,
                chain=["命中实时话术：开团日期", f"匹配品牌：{brand}", "售卖状态：售卖中", "调用第二项品牌清单话术"],
                score=95,
            )
        if status == "永久截团":
            return TalkMatch(
                answer="已经截团了不再卖了，看看别的。",
                link=None,
                chain=["命中实时话术：开团日期", f"匹配品牌：{brand}", "售卖状态：永久截团"],
                score=95,
            )
        schedule_match = match_open_group_schedule_text(question, config.open_group_knowledge, config, append_brand_reply=True)
        if schedule_match:
            return schedule_match

    if any(trigger_matches_question(trigger, question, "keyword") for trigger in config.brand_triggers):
        brand_or_category_reply = render_keyword_reply(question, config)
        if brand_or_category_reply:
            return TalkMatch(
                answer=brand_or_category_reply,
                link=None,
                chain=["命中实时话术：品牌清单", f"识别问题：{question}"],
                score=90,
            )
    return None


def match_fixed_talk(
    question: str,
    entry_title: str,
    entries: list[FixedTalkEntry],
    assets: list[AssetItem],
) -> TalkMatch | None:
    entry = next((item for item in entries if item.title == entry_title), None)
    if entry is None or not normalize_text(question):
        return None
    assets_by_id = {item.id: item for item in assets}
    for rule in entry.reply_rules:
        matched_keyword = next(
            (keyword for keyword in rule.keywords if normalize_text(keyword) in normalize_text(question)),
            "",
        )
        if not matched_keyword:
            continue
        if entry.triggers and not any(
            trigger_matches_question(trigger, question, "keyword") for trigger in entry.triggers
        ):
            continue
        matched_assets = [assets_by_id[asset_id] for asset_id in rule.asset_ids if asset_id in assets_by_id]
        answer = render_fixed_assets(matched_assets)
        if not answer:
            continue
        return TalkMatch(
            answer=answer,
            link=None,
            chain=[
                f"命中固定话术：{entry.title}",
                f"匹配关键词：{matched_keyword}",
                "回复素材：" + "、".join(item.title for item in matched_assets),
            ],
            score=90,
            assets=matched_assets,
        )
    return None


def render_fixed_assets(assets: list[AssetItem]) -> str:
    parts: list[str] = []
    for asset in assets:
        lines: list[str] = []
        has_paths = any(path for path in asset.paths)
        if has_paths:
            lines.append(f"素材：{asset.title}")
        if asset.description.strip():
            lines.append(asset.description.strip())
        lines.extend(f"image: {path}" for path in asset.paths if path)
        if lines:
            parts.append("\n".join(lines))
    return "\n---\n".join(parts)


def trigger_matches_question(trigger: str, question: str, variable_name: str) -> bool:
    trigger_text = normalize_text(trigger)
    question_text = normalize_text(question)
    if not trigger_text:
        return False
    token = "{" + variable_name + "}"
    if token not in trigger_text:
        return trigger_text in question_text
    before, _, after = trigger_text.partition(token)
    return (not before or before in question_text) and (not after or after in question_text)


def render_keyword_reply(question: str, config: RealtimeTalkConfig) -> str:
    aliases, brands_by_category = _talk_category_catalog()
    categories = extract_categories_from_question(question, aliases)
    if categories:
        brands = unique_terms(
            brand
            for category in categories
            for brand in brands_by_category.get(category, [])
        )
        replies = [render_brand_reply(brand, config) for brand in brands]
        return deduplicate_reply_parts(replies)
    brand = extract_brand_from_question(question, config)
    if brand:
        return render_brand_reply(brand, config)
    return ""


def render_brand_reply(brand: str, config: RealtimeTalkConfig) -> str:
    normalized_brand = normalize_brand(brand, config)
    normalized_key = normalize_text(normalized_brand)
    for rule in config.brand_reply_rules:
        if rule.keyword_type != "品牌" or normalize_text(rule.keyword) != normalized_key:
            continue
        reply = "\n".join(clean_terms(rule.reply_terms))
        if not reply:
            return ""
        supplemental_reply = rule.supplemental_reply.strip()
        return f"{reply}\n---\n{supplemental_reply}" if supplemental_reply else reply
    return ""


def deduplicate_reply_parts(replies: list[str]) -> str:
    unique_parts: list[str] = []
    seen: set[str] = set()
    for reply in replies:
        for part in re.split(r"\n\s*-{3,}\s*\n", reply.strip()):
            cleaned = part.strip()
            key = normalize_text(cleaned)
            if not cleaned or key in seen:
                continue
            seen.add(key)
            unique_parts.append(cleaned)
    return "\n---\n".join(unique_parts)


def match_open_group_schedule_text(
    question: str,
    content: str,
    config: RealtimeTalkConfig,
    *,
    append_brand_reply: bool = False,
) -> TalkMatch | None:
    if not content.strip():
        return None
    for entry in parse_open_group_entries(content):
        brand = find_open_group_brand_with_aliases(question, entry.content, config)
        if not brand:
            continue
        answer = render_open_group_answer(brand, entry.date_text)
        if append_brand_reply:
            brand_reply = render_brand_reply(brand, config)
            if brand_reply:
                answer = f"{answer}\n{brand_reply}"
            answer = f"{answer}\n先看看款式"
        return TalkMatch(
            answer=answer,
            link=None,
            chain=["命中实时话术：开团日期", f"匹配品牌：{brand}", f"开团日期：{entry.date_text}"],
            score=90,
        )
    return None


def find_sale_status_rule(brand: str, config: RealtimeTalkConfig) -> BrandSaleStatusRule | None:
    normalized_brand = normalize_text(brand)
    for rule in config.sale_status_rules:
        terms = [rule.brand, *rule.aliases]
        if any(normalize_text(term) == normalized_brand or normalize_text(term) in normalized_brand or normalized_brand in normalize_text(term) for term in terms if term):
            return rule
    return None


def extract_brand_from_question(question: str, config: RealtimeTalkConfig) -> str:
    candidates = all_known_brands(config)
    normalized_question = normalize_text(question)
    for candidate in candidates:
        value = normalize_text(candidate)
        if value and value in normalized_question:
            return normalize_brand(candidate, config)
    return ""


def extract_category_from_question(question: str, aliases_by_category: dict[str, list[str]] | None = None) -> str:
    categories = extract_categories_from_question(question, aliases_by_category)
    return categories[0] if categories else ""


def extract_categories_from_question(question: str, aliases_by_category: dict[str, list[str]] | None = None) -> list[str]:
    normalized_question = normalize_text(question)
    aliases_by_category = aliases_by_category or _talk_category_catalog()[0]
    matched: list[str] = []
    for category, aliases in aliases_by_category.items():
        terms = [category, *aliases, *semantic_category_terms(category, aliases)]
        if any(_category_term_matches_question(term, normalized_question) for term in terms if term):
            matched.append(category)
    return matched


def _category_term_matches_question(term: str, normalized_question: str) -> bool:
    normalized_term = normalize_text(term)
    if not normalized_term:
        return False
    if len(normalized_term) == 1:
        return normalized_question == normalized_term
    return normalized_term in normalized_question


def _talk_category_catalog() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    aliases = {category: list(values) for category, values in category_aliases().items()}
    brands = {category: list(values) for category, values in category_brands().items()}
    for category, indexed_brands in _indexed_category_brands().items():
        aliases.setdefault(category, [])
        current_brands = brands.setdefault(category, [])
        for brand in indexed_brands:
            if brand not in current_brands:
                current_brands.append(brand)
    return aliases, brands


def _indexed_category_brands() -> dict[str, list[str]]:
    global _INDEX_CATALOG_MTIME, _INDEX_CATEGORY_BRANDS
    try:
        mtime = _INDEX_CORPUS_PATH.stat().st_mtime
    except OSError:
        mtime = None
    if _INDEX_CATALOG_MTIME == mtime:
        return _INDEX_CATEGORY_BRANDS

    catalog: dict[str, list[str]] = {}
    if mtime is not None:
        try:
            with _INDEX_CORPUS_PATH.open(encoding="utf-8") as corpus:
                for line in corpus:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    text = str(payload.get("text", ""))
                    categories = category_tags_from_text(text)
                    brands = brand_tags_from_text(text)
                    for category in categories:
                        values = catalog.setdefault(category, [])
                        for brand in brands:
                            if brand not in values:
                                values.append(brand)
        except OSError:
            catalog = {}
    _INDEX_CATALOG_MTIME = mtime
    _INDEX_CATEGORY_BRANDS = catalog
    return catalog


def keyword_matches_question(keyword: str, question: str, config: RealtimeTalkConfig) -> bool:
    normalized_keyword = normalize_text(keyword)
    normalized_question = normalize_text(question)
    if normalized_keyword and normalized_keyword in normalized_question:
        return True
    normalized_brand = normalize_text(normalize_brand(keyword, config))
    return bool(normalized_brand and normalized_brand in normalized_question)


def normalize_brand(brand: str, config: RealtimeTalkConfig) -> str:
    value = str(brand or "").strip()
    normalized = normalize_text(value)
    for rule in config.sale_status_rules:
        terms = [rule.brand, *rule.aliases]
        if any(normalize_text(term) == normalized for term in terms if term):
            return rule.brand
    return value


def all_known_brands(config: RealtimeTalkConfig) -> list[str]:
    values: list[str] = []
    for brand_values in _talk_category_catalog()[1].values():
        values.extend(brand_values)
    for rule in config.brand_reply_rules:
        if rule.keyword_type == "品牌":
            values.append(rule.keyword)
    for rule in config.brand_alias_rules:
        values.extend([rule.brand, *rule.aliases])
    for rule in config.sale_status_rules:
        values.extend([rule.brand, *rule.aliases])
    return unique_terms([value for value in values if value])


def find_open_group_brand_with_aliases(question: str, schedule_text: str, config: RealtimeTalkConfig) -> str:
    brand = extract_brand_from_question(question, config)
    if brand and any(normalize_text(term) in normalize_text(schedule_text) for term in [brand, *brand_aliases(brand, config)]):
        return brand
    return find_open_group_brand(question, schedule_text)


def brand_aliases(brand: str, config: RealtimeTalkConfig) -> list[str]:
    normalized_brand = normalize_text(brand)
    aliases: list[str] = []
    for rule in config.sale_status_rules:
        if normalize_text(rule.brand) == normalized_brand:
            aliases.extend(rule.aliases)
    return aliases


def _default_open_group_knowledge_from_items(items: list[KnowledgeItem]) -> str:
    return next((item.content for item in items if "开团" in item.title or "开团" in item.content), "")


def _keyword_type(value: str) -> KeywordType:
    return value if value in {"品牌", "品类", "品牌别名"} else "品牌"


def _sale_status(value: str) -> SaleStatus:
    return value if value in {"售卖中", "暂时截团", "永久截团"} else "售卖中"


def score_link(question: str, link: TalkLink) -> float:
    normalized = normalize_text(question)
    score = 0.0
    for trigger in link.triggers:
        value = normalize_text(trigger)
        if not value:
            continue
        if value == normalized:
            score += 100
        elif value in normalized:
            score += 60 + min(len(value), 20)
        else:
            parts = [part for part in re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9]{2,}", value) if part]
            matched = sum(1 for part in parts if part in normalized)
            if parts and matched:
                score += matched / len(parts) * 25
    for category in link.categories:
        value = normalize_text(category)
        if value and value not in {"全部", "所有"} and value in normalized:
            score += 10
    if score > 0:
        score += min(link.priority, 100) / 100
    return score


def render_link_answer(link: TalkLink, knowledge: list[KnowledgeItem], assets: list[AssetItem]) -> str:
    if link.link_type == "fixed":
        return render_template(link.response_template)
    if link.link_type == "knowledge":
        content = "\n".join(item.content for item in knowledge).strip()
        template = link.knowledge_prompt.strip() or "请根据以下知识点回复：\n{knowledge}"
        return render_template(template, knowledge=content)
    if link.link_type == "image":
        if link.response_template.strip():
            return render_template(link.response_template)
        if assets:
            return f"已命中图片素材：{assets[0].title}"
        return "已命中图片素材链路，但还没有选择图片资源。"
    return ""


def select_knowledge(question: str, link: TalkLink, items: list[KnowledgeItem]) -> list[KnowledgeItem]:
    if link.knowledge_ids:
        by_id = {item.id: item for item in items}
        return [by_id[item_id] for item_id in link.knowledge_ids if item_id in by_id]
    if link.link_type != "knowledge":
        return []
    scored = [(score_text(question, item.title + "\n" + item.content + "\n" + " ".join(item.categories)), item) for item in items]
    return [item for score, item in sorted(scored, key=lambda value: value[0], reverse=True) if score > 0][:3]


def select_assets(link: TalkLink, items: list[AssetItem]) -> list[AssetItem]:
    if not link.asset_ids:
        return []
    by_id = {item.id: item for item in items}
    return [by_id[item_id] for item_id in link.asset_ids if item_id in by_id]


def match_open_group_schedule(question: str, items: list[KnowledgeItem]) -> TalkMatch | None:
    normalized_question = normalize_text(question)
    if not normalized_question:
        return None
    for item in items:
        if "开团" not in item.title and "开团" not in item.content:
            continue
        for entry in parse_open_group_entries(item.content):
            brand = find_open_group_brand(question, entry.content)
            if not brand:
                continue
            answer = render_open_group_answer(brand, entry.date_text)
            return TalkMatch(
                answer=answer,
                link=None,
                chain=[
                    f"识别问题：{question}",
                    f"命中知识：{item.title}",
                    f"匹配品牌：{brand}",
                    f"开团日期：{entry.date_text}",
                    "回复规则：今天开团回复今晚8点；未来开团回复日期并提示耐心等待",
                ],
                score=90,
                knowledge=[item],
            )
    return None


def parse_open_group_entries(content: str) -> list[OpenGroupEntry]:
    entries: list[OpenGroupEntry] = []
    current_date = ""
    current_lines: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matched_date = re.match(r"^(\d{1,2}\.\d{1,2})(?:\s*(.*))?$", line)
        if matched_date:
            if current_date:
                entries.append(OpenGroupEntry(current_date, "\n".join(current_lines).strip()))
            current_date = matched_date.group(1)
            current_lines = [matched_date.group(2).strip()] if matched_date.group(2) else []
            continue
        if current_date:
            current_lines.append(line)
    if current_date:
        entries.append(OpenGroupEntry(current_date, "\n".join(current_lines).strip()))
    return entries


def find_open_group_brand(question: str, schedule_text: str) -> str:
    normalized_schedule = normalize_text(schedule_text)
    for candidate in extract_question_brand_candidates(question):
        if candidate in normalized_schedule:
            return candidate
    normalized_question = normalize_text(question)
    for candidate in extract_schedule_brand_candidates(schedule_text):
        normalized_candidate = normalize_text(candidate)
        if len(normalized_candidate) >= 2 and normalized_candidate in normalized_question:
            return candidate
    return ""


def extract_question_brand_candidates(question: str) -> list[str]:
    text = normalize_text(question)
    noise = [
        "什么时候开团",
        "什么时候播",
        "下次是什么时候",
        "下次开团",
        "还有卖吗",
        "还有卖",
        "是哪天",
        "几号",
        "开团",
        "时间",
        "什么时候",
        "今天",
        "今晚",
        "下次",
        "还有",
        "会有",
        "有吗",
        "播吗",
        "播",
        "吗",
    ]
    for value in noise:
        text = text.replace(value, "")
    candidates = [part for part in re.split(r"[^a-z0-9\u4e00-\u9fff]+", text) if len(part) >= 2]
    return unique_terms([candidate for candidate in candidates if not is_generic_schedule_term(candidate)])


def extract_schedule_brand_candidates(schedule_text: str) -> list[str]:
    raw_parts = re.split(r"[、,&，,\s\n]+", schedule_text)
    candidates: list[str] = []
    for part in raw_parts:
        text = part.strip(" -:：；;。()（）")
        if len(normalize_text(text)) < 2:
            continue
        candidates.append(text)
        for marker in ["专场", "京东", "大量", "新品", "福利", "总裁", "中国区", "三件", "抽"]:
            if marker in text:
                candidates.append(text.split(marker, 1)[0])
        for suffix in ["指纹锁", "系统窗", "定制", "沙发", "卫浴", "浴霸", "系", "床"]:
            if text.endswith(suffix) and len(text) > len(suffix) + 1:
                candidates.append(text[: -len(suffix)])
    return unique_terms([candidate for candidate in candidates if not is_generic_schedule_term(candidate)])


def render_open_group_answer(brand: str, date_text: str) -> str:
    now = datetime.now()
    month_text, day_text = date_text.split(".", 1)
    group_date = datetime(now.year, int(month_text), int(day_text)).date()
    today = now.date()
    if group_date == today:
        return f"{brand}今晚8点开团。"
    if group_date > today:
        return f"{brand}{date_text}开团，耐心等待。"
    return f"{brand}{date_text}开团，已经过了，可以关注后续排期。"


def is_generic_schedule_term(value: str) -> bool:
    return normalize_text(value) in {
        "开团",
        "时间",
        "专场",
        "全品类",
        "家电",
        "家具",
        "家装",
        "厨电",
        "净水器",
        "空净",
        "卫浴",
        "灯具",
        "智能家居",
        "全屋定制",
        "单品bug价",
        "品牌多类折",
    }


def unique_terms(values: list[str]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = value.strip()
        normalized = normalize_text(term)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        terms.append(term)
    terms.sort(key=lambda item: len(normalize_text(item)), reverse=True)
    return terms


def build_chain(
    question: str,
    link: TalkLink,
    score: float,
    knowledge: list[KnowledgeItem],
    assets: list[AssetItem],
) -> list[str]:
    chain = [
        f"识别问题：{question}",
        f"命中链路：{link.title}",
        f"链路类型：{display_link_type(link.link_type)}",
        f"匹配分数：{score:.1f}",
    ]
    if link.triggers:
        chain.append("触发词：" + "、".join(link.triggers[:5]))
    if link.categories:
        chain.append("Tag：" + "、".join(link.categories))
    if knowledge:
        chain.append("引用知识：" + "、".join(item.title for item in knowledge))
    if assets:
        chain.append("图片素材：" + "、".join(item.title for item in assets))
    return chain


def render_template(template: str, **values: str) -> str:
    now = datetime.now()
    context = {
        "date_m_d": f"{now.month}.{now.day}",
        "date_ymd": now.strftime("%Y-%m-%d"),
        "knowledge": "",
    }
    context.update(values)
    return template.format(**context)


def score_text(question: str, text: str) -> float:
    normalized_question = normalize_text(question)
    normalized_text = normalize_text(text)
    if not normalized_question or not normalized_text:
        return 0.0
    score = 0.0
    if normalized_question in normalized_text:
        score += 50
    for term in re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9]{2,}", normalized_question):
        if term in normalized_text:
            score += 8
    return score


def display_link_type(link_type: LinkType) -> str:
    return {"fixed": "固定话术", "knowledge": "动态知识", "image": "图片素材"}.get(link_type, link_type)


def link_type_from_display(value: str) -> LinkType:
    mapping: dict[str, LinkType] = {"固定话术": "fixed", "动态知识": "knowledge", "图片素材": "image"}
    return mapping.get(value, "fixed")


def clean_terms(values: list[str] | str) -> list[str]:
    if isinstance(values, str):
        raw_values = re.split(r"[,，、\n]+", values)
    else:
        raw_values = values
    cleaned: list[str] = []
    for value in raw_values:
        text = str(value).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def clean_tags(values: list[str] | str, default: str = "其他") -> list[str]:
    tags = [tag for tag in clean_terms(values) if tag not in {"全部", "所有"}]
    return tags or [default]


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value).strip().lower())


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_id() -> str:
    return uuid4().hex


def _selected_fixed_titles(fixed_titles: list[str] | None) -> list[str]:
    if fixed_titles is None:
        return list(FIXED_TALK_TITLES)
    selected = [str(title).strip() for title in fixed_titles]
    return [title for title in FIXED_TALK_TITLES if title in selected]


def _exported_config_files(include_realtime: bool, fixed_titles: list[str]) -> list[str]:
    files: list[str] = []
    if include_realtime:
        files.append("realtime.json")
    if fixed_titles:
        files.extend(["fixed.json", "assets.json"])
    return files


def _assets_for_fixed_titles(
    assets: list[AssetItem],
    entries: list[FixedTalkEntry],
    fixed_titles: list[str],
) -> list[AssetItem]:
    referenced_ids = {
        asset_id
        for entry in entries
        for rule in entry.reply_rules
        for asset_id in rule.asset_ids
    }
    return [
        asset
        for asset in assets
        if asset.id in referenced_ids or any(title in asset.categories for title in fixed_titles)
    ]


def _read_zip_json_object(archive: zipfile.ZipFile, name: str) -> dict | None:
    if name not in archive.namelist():
        return None
    try:
        payload = json.loads(archive.read(name).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"ZIP 中的 {name} 不是有效 JSON。") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"ZIP 中的 {name} 格式不正确。")
    return payload


def _read_zip_json_list(archive: zipfile.ZipFile, name: str) -> list[dict] | None:
    if name not in archive.namelist():
        return None
    try:
        payload = json.loads(archive.read(name).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"ZIP 中的 {name} 不是有效 JSON。") from exc
    if not isinstance(payload, list):
        raise ValueError(f"ZIP 中的 {name} 格式不正确。")
    return payload


def _asset_from_import_payload(payload: dict, archive: zipfile.ZipFile, asset_dir: Path) -> AssetItem:
    asset_id = str(payload.get("id") or new_id())
    target_dir = asset_dir / asset_id
    target_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    prefix = f"assets/{asset_id}/"
    for member in sorted(name for name in archive.namelist() if name.startswith(prefix) and not name.endswith("/")):
        filename = Path(member).name
        if not filename:
            continue
        target = target_dir / filename
        target.write_bytes(archive.read(member))
        paths.append(str(target))
    return AssetItem(
        id=asset_id,
        title=str(payload.get("title", "") or "未命名素材").strip(),
        paths=paths,
        categories=clean_tags(payload.get("categories", [])),
        description=str(payload.get("description", "") or "").strip(),
        updated_at=str(payload.get("updated_at", "") or now_text()),
    )


def _read_json_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def _write_json_list(path: Path, payload: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
