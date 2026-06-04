from __future__ import annotations

import socket
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from customer_rag.config import RagConfig


LLAMA_CPP_DIR = Path("tools") / "llama.cpp"


@dataclass(frozen=True)
class LlamaServerPlan:
    enabled: bool
    executable: Path | None
    backend: str
    url: str
    args: list[str]
    reason: str = ""


def build_llama_server_plan(config: RagConfig, root: Path | None = None) -> LlamaServerPlan:
    root = root or Path.cwd()
    llm = config.llm
    url = llm.llama_server_url.rstrip("/")
    if llm.backend not in {"auto", "llama_cpp_server"}:
        return LlamaServerPlan(False, None, "disabled", url, [], "llama.cpp server 后端未启用")
    if not config.llm_model_path.exists():
        return LlamaServerPlan(False, None, "missing_model", url, [], f"GGUF 模型不存在：{config.llm_model_path}")

    executable, backend, reason = find_llama_server_executable(config, root)
    if executable is None:
        return LlamaServerPlan(False, None, backend, url, [], reason)

    args = [
        str(executable),
        "--host",
        llm.llama_server_host,
        "--port",
        str(llm.llama_server_port),
        "-m",
        str(config.llm_model_path),
        "-c",
        str(llm.n_ctx),
        "-t",
        str(llm.n_threads),
        "-b",
        str(llm.num_batch),
    ]
    if llm.n_gpu_layers:
        args.extend(["-ngl", str(llm.n_gpu_layers)])
    return LlamaServerPlan(True, executable, backend, url, args)


def find_llama_server_executable(config: RagConfig, root: Path | None = None) -> tuple[Path | None, str, str]:
    root = root or Path.cwd()
    explicit = str(config.llm.llama_server_executable or "").strip()
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = root / path
        if path.exists():
            return path, "custom", ""
        return None, "custom", f"配置的 llama-server 不存在：{path}"

    requested = config.llm.llama_server_backend.lower()
    backend_order = _backend_order(requested)
    for backend in backend_order:
        for candidate in _backend_candidates(root, backend):
            if candidate.exists():
                return candidate, backend, ""
    return None, requested, _missing_executable_message(root, backend_order)


def is_llama_server_healthy(config: RagConfig, timeout: float = 1.0) -> bool:
    url = config.llm.llama_server_url.rstrip("/")
    for path in ("/health", "/v1/models", "/props"):
        try:
            with urllib.request.urlopen(url + path, timeout=timeout) as response:
                if 200 <= response.status < 500:
                    return True
        except (OSError, urllib.error.URLError, TimeoutError):
            continue
    return False


def is_port_listening(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _backend_order(requested: str) -> list[str]:
    if requested in {"cpu", "cuda", "vulkan"}:
        return [requested]
    order = []
    if _has_nvidia_gpu():
        order.append("cuda")
    order.extend(["vulkan", "cpu"])
    return order


def _backend_candidates(root: Path, backend: str) -> list[Path]:
    base = root / LLAMA_CPP_DIR
    names = ["llama-server.exe", "server.exe"]
    return [base / backend / name for name in names] + [base / name for name in names]


def _has_nvidia_gpu() -> bool:
    try:
        result = subprocess.run(
            ["nvidia-smi"],
            text=True,
            capture_output=True,
            timeout=2,
            creationflags=0x08000000,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _missing_executable_message(root: Path, backends: list[str]) -> str:
    folders = "、".join(str(root / LLAMA_CPP_DIR / backend / "llama-server.exe") for backend in backends)
    return f"未找到 llama-server.exe，请放到：{folders}"
