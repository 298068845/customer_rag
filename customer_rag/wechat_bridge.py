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


DEFAULT_PROMPT_SUFFIX = """

微信发送格式要求：
- 如果回答包含多个商品、多个方案或多个可独立发送的段落，请用单独一行 --- 分隔。
- 不要使用 Markdown 代码块包裹答案。
- 每一段都要能直接粘贴发送给客户。
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the local RAG system and write the answer for WeChat sending.")
    parser.add_argument("--question-file", required=True, type=Path)
    parser.add_argument("--output-file", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--log-file", type=Path)
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    log_file = args.log_file or project_root / "wechatExtension" / "rag-bridge.log"

    try:
        os.chdir(project_root)
        question = args.question_file.read_text(encoding="utf-8-sig").strip()
        if not question:
            raise ValueError("question file is empty")

        pipeline = RagPipeline(load_config(project_root / "config.yaml"))
        system_prompt = load_system_prompt(project_root / "data" / "index" / "prompt_settings.json")
        result = pipeline.ask(question, system_prompt=system_prompt + DEFAULT_PROMPT_SUFFIX)
        answer = format_wechat_answer(result.answer)

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


def write_log(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
