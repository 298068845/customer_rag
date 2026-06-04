from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from customer_rag.attributes import extract_attributes
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
    attributes: dict = field(default_factory=dict)


BuildProgressCallback = Callable[[int, str], None]


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
        self._query_embedding_cache: dict[str, Any] = {}

    def build(self, chunks: list[Chunk], progress_callback: BuildProgressCallback | None = None) -> None:
        def emit(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(max(0, min(percent, 100)), message)

        if not chunks:
            self.clear()
            emit(100, "没有可索引的片段，已清空向量索引")
            return

        emit(1, f"准备编码 {len(chunks)} 个文本片段")
        embeddings = self._embed(
            [chunk.text for chunk in chunks],
            progress_callback=lambda percent, message: emit(1 + int(percent * 0.88), message),
        )
        emit(92, "正在创建 FAISS 内存索引")
        faiss = _get_faiss()
        index = faiss.IndexFlatIP(embeddings.shape[1])
        index.add(embeddings)

        emit(95, "正在写入索引文件")
        self.index_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(self.index_path))
        with self.meta_path.open("w", encoding="utf-8") as fp:
            total = len(chunks)
            step = max(1, total // 20)
            for index_in_file, chunk in enumerate(chunks, start=1):
                fp.write(json.dumps(asdict(chunk), ensure_ascii=False) + "\n")
                if index_in_file % step == 0 or index_in_file == total:
                    emit(95 + int(index_in_file / total * 4), f"正在写入片段元数据 {index_in_file}/{total}")

        self.index = index
        self.chunks = chunks
        emit(100, f"向量索引写入完成：{len(chunks)} 个片段")

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
                if not payload.get("attributes"):
                    payload["attributes"] = extract_attributes(str(payload.get("text", "")))
                self.chunks.append(Chunk(**payload))

    def search(self, query: str, top_k: int) -> list[RetrievedChunk]:
        if self.index is None:
            self.load()
        assert self.index is not None

        query_embedding = self._embed_query(query)
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
                    attributes=chunk.attributes,
                )
            )
        return results

    def _embed(self, texts: list[str], progress_callback: BuildProgressCallback | None = None) -> Any:
        def emit(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(max(0, min(percent, 100)), message)

        np = _get_numpy()
        batches = []
        total = len(texts)
        emit(0, "正在加载 embedding 模型")
        model = self._get_model()
        emit(2, f"embedding 模型已加载，开始编码 {total} 个片段")
        for start in range(0, total, self.batch_size):
            batch = texts[start : start + self.batch_size]
            embeddings = model.encode(
                batch,
                normalize_embeddings=True,
                show_progress_bar=False,
                batch_size=self.batch_size,
            )
            batches.append(np.asarray(embeddings, dtype="float32"))
            done = min(start + len(batch), total)
            emit(2 + int(done / total * 98), f"正在生成向量 {done}/{total}")
        return np.vstack(batches)

    def _embed_query(self, query: str) -> Any:
        cached = self._query_embedding_cache.get(query)
        if cached is not None:
            return cached
        embedding = self._embed([query])
        self._query_embedding_cache[query] = embedding
        if len(self._query_embedding_cache) > 128:
            oldest_key = next(iter(self._query_embedding_cache))
            self._query_embedding_cache.pop(oldest_key, None)
        return embedding

    def clear(self) -> None:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        for path in (self.index_path, self.meta_path):
            if path.exists():
                path.unlink()
        self.index = None
        self.chunks = []
        self._query_embedding_cache.clear()

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
