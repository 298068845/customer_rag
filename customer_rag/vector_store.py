from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from customer_rag.splitter import Chunk


@dataclass(frozen=True)
class RetrievedChunk:
    text: str
    source: str
    title: str
    location: str
    score: float
    image_paths: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


class VectorStore:
    def __init__(self, index_dir: Path, embedding_model_path: Path, batch_size: int = 32):
        self.index_dir = index_dir
        self.embedding_model_path = embedding_model_path
        self.batch_size = max(1, batch_size)
        self.index_path = index_dir / "faiss.index"
        self.meta_path = index_dir / "chunks.jsonl"
        self.model: Any | None = None
        self.index: Any | None = None
        self.chunks: list[Chunk] = []

    def build(self, chunks: list[Chunk]) -> None:
        if not chunks:
            self.clear()
            return

        embeddings = self._embed([chunk.text for chunk in chunks])
        faiss = _get_faiss()
        index = faiss.IndexFlatIP(embeddings.shape[1])
        index.add(embeddings)

        self.index_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(self.index_path))
        with self.meta_path.open("w", encoding="utf-8") as fp:
            for chunk in chunks:
                fp.write(json.dumps(asdict(chunk), ensure_ascii=False) + "\n")

        self.index = index
        self.chunks = chunks

    def load(self) -> None:
        if not self.index_path.exists() or not self.meta_path.exists():
            raise FileNotFoundError("向量索引不存在，请先导入语料并重建索引。")

        faiss = _get_faiss()
        self.index = faiss.read_index(str(self.index_path))
        self.chunks = []
        with self.meta_path.open("r", encoding="utf-8") as fp:
            for line in fp:
                payload = json.loads(line)
                payload.setdefault("image_paths", [])
                payload.setdefault("tags", [])
                self.chunks.append(Chunk(**payload))

    def search(self, query: str, top_k: int) -> list[RetrievedChunk]:
        if self.index is None:
            self.load()
        assert self.index is not None

        query_embedding = self._embed([query])
        scores, indices = self.index.search(query_embedding, top_k)
        results: list[RetrievedChunk] = []
        for score, index in zip(scores[0], indices[0]):
            if index < 0:
                continue
            chunk = self.chunks[int(index)]
            results.append(
                RetrievedChunk(
                    text=chunk.text,
                    source=chunk.source,
                    title=chunk.title,
                    location=chunk.location,
                    score=float(score),
                    image_paths=chunk.image_paths,
                    tags=chunk.tags,
                )
            )
        return results

    def _embed(self, texts: list[str]) -> Any:
        np = _get_numpy()
        batches = []
        model = self._get_model()
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            embeddings = model.encode(
                batch,
                normalize_embeddings=True,
                show_progress_bar=False,
                batch_size=self.batch_size,
            )
            batches.append(np.asarray(embeddings, dtype="float32"))
        return np.vstack(batches)

    def clear(self) -> None:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        for path in (self.index_path, self.meta_path):
            if path.exists():
                path.unlink()
        self.index = None
        self.chunks = []

    def _get_model(self) -> Any:
        if self.model is None:
            if not self.embedding_model_path.exists():
                raise RuntimeError(
                    f"本地 embedding 模型不存在：{self.embedding_model_path}。"
                    "语料已可管理；如需问答检索，请先把 embedding 模型放到该目录，或修改 config.yaml。"
                )
            try:
                from sentence_transformers import SentenceTransformer
            except ModuleNotFoundError as exc:
                raise RuntimeError("缺少向量模型依赖，请先运行：pip install sentence-transformers") from exc
            self.model = SentenceTransformer(str(self.embedding_model_path))
        return self.model


def _get_faiss() -> Any:
    try:
        import faiss
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 FAISS 向量库依赖，请先运行：pip install faiss-cpu") from exc
    return faiss


def _get_numpy() -> Any:
    try:
        import numpy as np
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 numpy 依赖，请先运行：pip install numpy") from exc
    return np
