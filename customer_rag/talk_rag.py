from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from customer_rag.category_config import category_aliases, category_brands


LinkType = Literal["fixed", "knowledge", "image"]


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


class TalkRagEngine:
    def __init__(self, store: TalkRagStore | None = None):
        self.store = store or TalkRagStore()

    def ask(self, question: str) -> TalkMatch:
        realtime_match = match_realtime_talk(question, self.store.load_realtime_config())
        if realtime_match:
            return realtime_match
        return TalkMatch(
            answer="暂时没有命中合适的实时话术。",
            link=None,
            chain=["未命中实时话术", "返回兜底话术"],
            score=0,
        )

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
    return RealtimeTalkConfig(open_group_knowledge=_default_open_group_knowledge_from_items(default_knowledge()))


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
            )
            for item in payload.get("brand_reply_rules", [])
            if isinstance(item, dict)
        ],
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
    category = extract_category_from_question(question)
    if category:
        brands = category_brands().get(category, [])
        return "\n".join(f"@小助理 {brand}清单" for brand in brands)
    brand = extract_brand_from_question(question, config)
    if brand:
        return render_brand_reply(brand, config)
    return ""


def render_brand_reply(brand: str, config: RealtimeTalkConfig) -> str:
    normalized_brand = normalize_brand(brand, config)
    return f"@小助理 {normalized_brand}清单" if normalized_brand else ""


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
    for candidate in extract_question_brand_candidates(question):
        normalized = normalize_brand(candidate, config)
        if normalized:
            return normalized
    return ""


def extract_category_from_question(question: str) -> str:
    normalized_question = normalize_text(question)
    for category, aliases in category_aliases().items():
        terms = [category, *aliases]
        if any(normalize_text(term) in normalized_question for term in terms if term):
            return category
    return ""


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
    for brand_values in category_brands().values():
        values.extend(brand_values)
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
