from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import yaml

APP_DATA_DIR_NAME = "CustomerRAG"
CONFIG_FILE_NAME = "config.yaml"
DEFAULT_CONFIG_FILE_NAME = "config.default.yaml"
CONFIG_DIR_ENV = "CUSTOMER_RAG_CONFIG_DIR"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
    n_parallel: int = 1
    prompt_cache_mb: int = 1024


@dataclass(frozen=True)
class RagConfig:
    raw_data_dir: Path
    index_dir: Path
    embedding_model_path: Path
    llm_model_path: Path
    tools_dir: Path = Path("tools")
    talk_data_dir: Path = Path("data/talk_rag")
    chunk_size: int = 700
    chunk_overlap: int = 120
    embedding_batch_size: int = 32
    top_k: int = 5
    search_timeout_seconds: float = 4.0
    product_search_timeout_seconds: float = 6.0
    precise_search_timeout_seconds: float = 8.0
    llm: LlmConfig = LlmConfig()


def user_config_dir() -> Path:
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return (Path(local_app_data) / APP_DATA_DIR_NAME).resolve()
    return (Path.home() / "AppData" / "Local" / APP_DATA_DIR_NAME).resolve()


def default_config_template_path() -> Path:
    return PROJECT_ROOT / DEFAULT_CONFIG_FILE_NAME


def resolve_config_path(path: str | Path | None = None) -> Path:
    if path is not None and str(path) not in {"", CONFIG_FILE_NAME}:
        return Path(path)
    project_config = PROJECT_ROOT / CONFIG_FILE_NAME
    if project_config.exists():
        return project_config
    return user_config_dir() / CONFIG_FILE_NAME


def ensure_user_config(path: Path) -> None:
    if path.exists() or path.resolve() == (PROJECT_ROOT / CONFIG_FILE_NAME).resolve():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    root = user_config_dir()
    template = default_config_template_path()
    if template.exists():
        data = yaml.safe_load(template.read_text(encoding="utf-8")) or {}
    else:
        data = {}
    data.update(
        {
            "raw_data_dir": str(root / "raw"),
            "index_dir": str(root / "index"),
            "talk_data_dir": str(root / "talk_rag"),
            "tools_dir": str(root / "tools"),
            "embedding_model_path": str(data.get("embedding_model_path", "")),
            "llm_model_path": str(data.get("llm_model_path", "")),
        }
    )
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def load_config(path: str | Path | None = None) -> RagConfig:
    config_path = resolve_config_path(path)
    ensure_user_config(config_path)
    data: dict[str, Any] = {}
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    llm_data = data.get("llm", {}) or {}
    return RagConfig(
        raw_data_dir=Path(data.get("raw_data_dir", "data/raw")),
        index_dir=Path(data.get("index_dir", "data/index")),
        embedding_model_path=Path(data.get("embedding_model_path", "models/embeddings/bge-small-zh-v1.5")),
        llm_model_path=Path(data.get("llm_model_path", "models/llm/model.gguf")),
        tools_dir=Path(data.get("tools_dir", "tools")),
        talk_data_dir=Path(data.get("talk_data_dir", "data/talk_rag")),
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
            n_parallel=int(llm_data.get("n_parallel", 1)),
            prompt_cache_mb=int(llm_data.get("prompt_cache_mb", 1024)),
        ),
    )


def save_machine_config(
    updates: dict[str, Any],
    llm_updates: dict[str, Any] | None = None,
    path: str | Path | None = None,
) -> None:
    config_path = resolve_config_path(path)
    ensure_user_config(config_path)
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
