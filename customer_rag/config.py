from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class LlmConfig:
    backend: str = "auto"
    n_ctx: int = 2048
    n_threads: int = 4
    temperature: float = 0.2
    max_tokens: int = 512
    num_batch: int = 128
    keep_alive: str = "0s"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "deepseek-r1:1.5b"
    llama_server_url: str = "http://127.0.0.1:8081"
    llama_server_host: str = "127.0.0.1"
    llama_server_port: int = 8081
    llama_server_executable: str = ""
    llama_server_backend: str = "auto"
    n_gpu_layers: int = 0


@dataclass(frozen=True)
class RagConfig:
    raw_data_dir: Path
    index_dir: Path
    embedding_model_path: Path
    llm_model_path: Path
    chunk_size: int = 700
    chunk_overlap: int = 120
    embedding_batch_size: int = 32
    top_k: int = 5
    search_timeout_seconds: float = 4.0
    product_search_timeout_seconds: float = 6.0
    precise_search_timeout_seconds: float = 8.0
    llm: LlmConfig = LlmConfig()


def load_config(path: str | Path = "config.yaml") -> RagConfig:
    config_path = Path(path)
    data: dict[str, Any] = {}
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    llm_data = data.get("llm", {}) or {}
    return RagConfig(
        raw_data_dir=Path(data.get("raw_data_dir", "data/raw")),
        index_dir=Path(data.get("index_dir", "data/index")),
        embedding_model_path=Path(data.get("embedding_model_path", "models/embeddings/bge-small-zh-v1.5")),
        llm_model_path=Path(data.get("llm_model_path", "models/llm/model.gguf")),
        chunk_size=int(data.get("chunk_size", 700)),
        chunk_overlap=int(data.get("chunk_overlap", 120)),
        embedding_batch_size=int(data.get("embedding_batch_size", 32)),
        top_k=int(data.get("top_k", 5)),
        search_timeout_seconds=float(data.get("search_timeout_seconds", 4.0)),
        product_search_timeout_seconds=float(data.get("product_search_timeout_seconds", 6.0)),
        precise_search_timeout_seconds=float(data.get("precise_search_timeout_seconds", 8.0)),
        llm=LlmConfig(
            backend=str(llm_data.get("backend", "auto")),
            n_ctx=int(llm_data.get("n_ctx", 2048)),
            n_threads=int(llm_data.get("n_threads", 4)),
            temperature=float(llm_data.get("temperature", 0.2)),
            max_tokens=int(llm_data.get("max_tokens", 512)),
            num_batch=int(llm_data.get("num_batch", 128)),
            keep_alive=str(llm_data.get("keep_alive", "0s")),
            ollama_url=str(llm_data.get("ollama_url", "http://localhost:11434")),
            ollama_model=str(llm_data.get("ollama_model", "deepseek-r1:1.5b")),
            llama_server_url=str(llm_data.get("llama_server_url", "http://127.0.0.1:8081")),
            llama_server_host=str(llm_data.get("llama_server_host", "127.0.0.1")),
            llama_server_port=int(llm_data.get("llama_server_port", 8081)),
            llama_server_executable=str(llm_data.get("llama_server_executable", "")),
            llama_server_backend=str(llm_data.get("llama_server_backend", "auto")),
            n_gpu_layers=int(llm_data.get("n_gpu_layers", 0)),
        ),
    )


def save_machine_config(
    updates: dict[str, Any],
    llm_updates: dict[str, Any] | None = None,
    path: str | Path = "config.yaml",
) -> None:
    config_path = Path(path)
    data: dict[str, Any] = {}
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    for key, value in updates.items():
        data[key] = value
    if llm_updates:
        llm_data = data.get("llm", {}) or {}
        for key, value in llm_updates.items():
            llm_data[key] = value
        data["llm"] = llm_data
    config_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
