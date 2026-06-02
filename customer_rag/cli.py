from __future__ import annotations

import argparse

from customer_rag.config import load_config
from customer_rag.pipeline import RagPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="本地腾讯文档 RAG")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("ingest", help="读取 data/raw 并构建向量索引")

    ask_parser = subparsers.add_parser("ask", help="向本地知识库提问")
    ask_parser.add_argument("question", help="问题")

    args = parser.parse_args()
    pipeline = RagPipeline(load_config())

    if args.command == "ingest":
        stats = pipeline.ingest()
        print(f"索引构建完成：{stats['documents']} 个文档，{stats['chunks']} 个片段")
    elif args.command == "ask":
        result = pipeline.ask(args.question)
        print(result.answer)
        print("\n引用片段：")
        for i, source in enumerate(result.sources, start=1):
            print(f"{i}. {source.location}  score={source.score:.3f}")


if __name__ == "__main__":
    main()

