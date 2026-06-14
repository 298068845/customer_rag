from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from customer_rag.config import load_config
from customer_rag.category_config import category_aliases
from customer_rag.category_config import category_brands
from customer_rag.pipeline import RagPipeline
from customer_rag.prompt_defaults import DEFAULT_SYSTEM_PROMPT
from customer_rag.talk_rag import TalkRagEngine


DEFAULT_PROMPT_SUFFIX = """

微信发送格式要求：
- 如果回答包含多个商品、多个方案或多个可独立发送的段落，请用单独一行 --- 分隔。
- 不要使用 Markdown 代码块包裹答案。
- 每一段都要能直接粘贴发送给客户。
"""

FALLBACK_CONTROL_MARKER = "__RAG_FUZZY_FALLBACK__"
QUERY_CACHE_VERSION = 1
QUERY_CACHE_TTL_SECONDS = 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the local RAG system and write the answer for WeChat sending.")
    parser.add_argument("--question-file", type=Path)
    parser.add_argument("--output-file", type=Path)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--tags", default="")
    parser.add_argument("--brand", default="")
    parser.add_argument("--brands-file", type=Path)
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--filters-file", type=Path)
    parser.add_argument("--talk-only", action="store_true")
    parser.add_argument("--talk-shortcuts-file", type=Path)
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    log_file = args.log_file or project_root / "wechatExtension" / "rag-bridge.log"

    try:
        os.chdir(project_root)
        if args.filters_file:
            pipeline = RagPipeline(load_config(project_root / "config.yaml"))
            write_filter_rows(args.filters_file, pipeline)
            write_log(log_file, f"OK\nfilters={args.filters_file}\n")
            return 0

        if not args.question_file or not args.output_file:
            raise ValueError("question-file and output-file are required")
        question = args.question_file.read_text(encoding="utf-8-sig").strip()
        if not question:
            raise ValueError("question file is empty")

        if args.talk_only:
            engine = TalkRagEngine()
            result = engine.ask(question)
            answer = format_wechat_answer(result.answer)
            args.output_file.parent.mkdir(parents=True, exist_ok=True)
            args.output_file.write_text(answer, encoding="utf-8")
            if args.talk_shortcuts_file:
                shortcut_answers = [format_wechat_answer(item.answer) for item in engine.ask_shortcuts(question)]
                args.talk_shortcuts_file.parent.mkdir(parents=True, exist_ok=True)
                args.talk_shortcuts_file.write_text("\n__TALK_SHORTCUT__\n".join(shortcut_answers), encoding="utf-8")
            write_log(log_file, f"OK\nmode=talk-only\nquestion={question}\noutput={args.output_file}\n")
            return 0

        config = load_config(project_root / "config.yaml")
        if args.top_k in {5, 10}:
            config = replace(config, top_k=args.top_k)
        system_prompt = load_system_prompt(project_root / "data" / "index" / "prompt_settings.json")
        selected_brand = args.brand.strip()
        effective_question = question if not selected_brand else f"{question}\nbrand:{selected_brand}"
        parsed_tags = parse_tags(args.tags)
        cache_key = query_cache_key(
            project_root,
            question=question,
            effective_question=effective_question,
            selected_brand=selected_brand,
            tags=parsed_tags,
            top_k=config.top_k,
            system_prompt=system_prompt,
            brands_seed=read_text_or_empty(args.brands_file) if selected_brand and args.brands_file else "",
        )
        cached = read_query_cache(project_root, cache_key)
        if cached is not None:
            args.output_file.parent.mkdir(parents=True, exist_ok=True)
            args.output_file.write_text(str(cached.get("answer", "")), encoding="utf-8")
            if args.brands_file:
                args.brands_file.parent.mkdir(parents=True, exist_ok=True)
                args.brands_file.write_text(str(cached.get("brands", "")), encoding="utf-8")
            write_log(log_file, f"OK\ncache=hit\nquestion={question}\nbrand={selected_brand}\noutput={args.output_file}\n")
            return 0

        pipeline = RagPipeline(config)
        system_prompt = load_system_prompt(project_root / "data" / "index" / "prompt_settings.json")
        selected_brand = args.brand.strip()
        effective_question = question if not selected_brand else f"{question}\n指定品牌：{selected_brand}"
        result = pipeline.ask(
            effective_question,
            system_prompt=system_prompt + DEFAULT_PROMPT_SUFFIX,
            tags=parsed_tags,
        )
        answer = format_wechat_answer(result.answer)
        if result.fallback:
            answer = FALLBACK_CONTROL_MARKER + "\n" + answer

        args.output_file.parent.mkdir(parents=True, exist_ok=True)
        args.output_file.write_text(answer, encoding="utf-8")
        brands_text = ""
        if args.brands_file:
            write_query_brands(args.brands_file, result.sources, selected_brand, answer=answer)
            brands_text = read_text_or_empty(args.brands_file)
        write_query_cache(project_root, cache_key, answer=answer, brands=brands_text)
        write_log(log_file, f"OK\ncache=miss\nquestion={question}\nbrand={selected_brand}\noutput={args.output_file}\n")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI bridge should log any user-facing failure.
        write_log(log_file, f"ERROR\n{type(exc).__name__}: {exc}\n")
        return 1


def load_system_prompt(path: Path) -> str:
    if not path.exists():
        return DEFAULT_SYSTEM_PROMPT
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_SYSTEM_PROMPT
    return str(payload.get("system_prompt", "")).strip() or DEFAULT_SYSTEM_PROMPT


def format_wechat_answer(answer: str) -> str:
    cleaned = answer.strip()
    cleaned = re.sub(r"```(?:\w+)?\s*", "", cleaned)
    cleaned = cleaned.replace("```", "")
    cleaned = re.sub(r"(?m)^\s*[-—_]{3,}\s*$", "---", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned or "资料中未找到相关信息"


def parse_tags(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,，;；|、\s]+", value or "") if part.strip()]


def query_cache_key(
    project_root: Path,
    *,
    question: str,
    effective_question: str,
    selected_brand: str,
    tags: list[str],
    top_k: int,
    system_prompt: str,
    brands_seed: str = "",
) -> str:
    payload = {
        "version": QUERY_CACHE_VERSION,
        "question": question,
        "effective_question": effective_question,
        "selected_brand": selected_brand,
        "tags": tags,
        "top_k": top_k,
        "system_prompt_sha1": _sha1_text(system_prompt + DEFAULT_PROMPT_SUFFIX),
        "brands_seed_sha1": _sha1_text(brands_seed),
        "config": file_signature(project_root / "config.yaml"),
        "corpus": file_signature(project_root / "data" / "index" / "corpus.jsonl"),
        "prompt": file_signature(project_root / "data" / "index" / "prompt_settings.json"),
        "category_aliases": file_signature(project_root / "category_aliases.yaml"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def read_query_cache(project_root: Path, key: str) -> dict | None:
    path = query_cache_path(project_root, key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != QUERY_CACHE_VERSION:
        return None
    created_at = float(payload.get("created_at") or 0)
    if QUERY_CACHE_TTL_SECONDS > 0 and time.time() - created_at > QUERY_CACHE_TTL_SECONDS:
        return None
    return payload


def write_query_cache(project_root: Path, key: str, *, answer: str, brands: str) -> None:
    cache_dir = query_cache_dir(project_root)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cleanup_query_cache(cache_dir)
    path = query_cache_path(project_root, key)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    payload = {
        "version": QUERY_CACHE_VERSION,
        "created_at": time.time(),
        "answer": answer,
        "brands": brands,
    }
    try:
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def cleanup_query_cache(cache_dir: Path) -> None:
    deadline = time.time() - QUERY_CACHE_TTL_SECONDS if QUERY_CACHE_TTL_SECONDS > 0 else None
    try:
        files = sorted(cache_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return
    for index, path in enumerate(files):
        try:
            if index >= 200 or (deadline is not None and path.stat().st_mtime < deadline):
                path.unlink()
        except OSError:
            pass


def query_cache_dir(project_root: Path) -> Path:
    return project_root / "data" / "index" / "query_cache"


def query_cache_path(project_root: Path, key: str) -> Path:
    return query_cache_dir(project_root) / f"{key}.json"


def file_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def read_text_or_empty(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def _sha1_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def write_filter_rows(path: Path, pipeline: RagPipeline) -> None:
    rows: set[tuple[str, str, str]] = set()
    for item in pipeline.list_corpus():
        primary = primary_filter_label(item.tags)
        if not primary:
            continue
        brand = field_value(item.text, "品牌")
        category = field_value(item.text, "品类")
        if not valid_filter_term(brand):
            brand = ""
        if not valid_filter_term(category):
            category = ""
        if brand or category:
            rows.add((primary, brand, category))
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join("\t".join(row) for row in sorted(rows))
    path.write_text(content, encoding="utf-8")


def write_query_brands(path: Path, sources: list, selected_brand: str = "", *, answer: str = "") -> None:
    brands: list[str] = []
    if selected_brand and path.exists():
        try:
            for part in path.read_text(encoding="utf-8-sig").splitlines():
                add_unique(brands, part)
        except OSError:
            brands = []
    brand_map = category_brands()
    category_lookup = {category.lower(): category for category in brand_map}
    alias_lookup = {
        alias.lower(): category
        for category, aliases in category_aliases().items()
        for alias in aliases
    }
    source_brands: list[str] = []
    source_categories: list[str] = []
    for source in sources:
        text = getattr(source, "text", "")
        category = field_value(text, "\u54c1\u7c7b")
        category_key = category_lookup.get(category.lower()) or alias_lookup.get(category.lower())
        brand = field_value(text, "\u54c1\u724c")
        if valid_filter_term(brand):
            add_unique(source_brands, brand)
        if category_key:
            add_unique(source_categories, category_key)
        if not source_brands and category_key:
            for brand in brand_map.get(category_key, []):
                add_unique(brands, brand)
    if source_brands:
        brands = []
        for brand in source_brands:
            add_unique(brands, brand)
        for category in source_categories:
            for brand in brand_map.get(category, []):
                add_unique(brands, brand)
    elif not brands:
        for brand in source_brands:
            add_unique(brands, brand)
    for brand in extract_brands_from_answer(answer):
        add_unique(brands, brand)
    if selected_brand:
        add_unique(brands, selected_brand, prepend=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(brands), encoding="utf-8")


def extract_brands_from_answer(answer: str) -> list[str]:
    text = str(answer or "")
    if not text:
        return []
    known_brands = sorted(
        {brand for values in category_brands().values() for brand in values if valid_filter_term(brand)},
        key=len,
        reverse=True,
    )
    brands: list[str] = []
    for brand in known_brands:
        if any(part.strip().startswith(brand) for part in re.split(r"\n\s*-{3,}\s*\n", text)):
            add_unique(brands, brand)
    return brands


def add_unique(values: list[str], value: str, *, prepend: bool = False) -> None:
    value = str(value or "").strip()
    if not value or value in values:
        return
    if prepend:
        values.insert(0, value)
    else:
        values.append(value)


def primary_filter_label(tags: list[str]) -> str:
    tag_set = set(tags or [])
    if "家电" in tag_set:
        return "家电"
    if "家装" in tag_set:
        return "家装"
    if "家具" in tag_set or "家居" in tag_set:
        return "家具"
    return ""


def field_value(text: str, field: str) -> str:
    match = re.search(rf"(?:^|[；\n])\s*{re.escape(field)}\s*[:：]\s*([^；\n]+)", text or "")
    return match.group(1).strip() if match else ""


def valid_filter_term(value: str) -> bool:
    value = str(value or "").strip()
    if not value or len(value) > 32:
        return False
    if value.upper() in {"#REF!", "#N/A", "NULL", "NONE"}:
        return False
    if re.fullmatch(r"[\d\s._-]+", value):
        return False
    if re.search(r"https?://|清单|商品|活动|说明|注意|机制|流程|链接", value):
        return False
    return True


def write_log(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
