from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from customer_rag.config import load_config
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the local RAG system and write the answer for WeChat sending.")
    parser.add_argument("--question-file", type=Path)
    parser.add_argument("--output-file", type=Path)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--tags", default="")
    parser.add_argument("--filters-file", type=Path)
    parser.add_argument("--talk-only", action="store_true")
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
            result = TalkRagEngine().ask(question)
            answer = format_wechat_answer(result.answer)
            args.output_file.parent.mkdir(parents=True, exist_ok=True)
            args.output_file.write_text(answer, encoding="utf-8")
            write_log(log_file, f"OK\nmode=talk-only\nquestion={question}\noutput={args.output_file}\n")
            return 0

        pipeline = RagPipeline(load_config(project_root / "config.yaml"))
        system_prompt = load_system_prompt(project_root / "data" / "index" / "prompt_settings.json")
        result = pipeline.ask(
            question,
            system_prompt=system_prompt + DEFAULT_PROMPT_SUFFIX,
            tags=parse_tags(args.tags),
        )
        answer = format_wechat_answer(result.answer)
        if result.fallback:
            answer = FALLBACK_CONTROL_MARKER + "\n" + answer

        args.output_file.parent.mkdir(parents=True, exist_ok=True)
        args.output_file.write_text(answer, encoding="utf-8")
        write_log(log_file, f"OK\nquestion={question}\noutput={args.output_file}\n")
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
