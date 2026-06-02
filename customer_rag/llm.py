from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from customer_rag.config import LlmConfig
from customer_rag.vector_store import RetrievedChunk


class LocalLlm:
    def __init__(self, model_path: Path, config: LlmConfig):
        self.model_path = model_path
        self.config = config
        self._llm = None

    @property
    def available(self) -> bool:
        return self.model_path.exists()

    def answer(self, question: str, sources: list[RetrievedChunk], system_prompt: str | None = None) -> str:
        if self.config.backend in {"ollama", "auto"}:
            if self.config.backend == "ollama" or not self.available:
                ollama_answer = self._answer_with_ollama(question, sources, system_prompt)
                if ollama_answer:
                    return ollama_answer

        if not self.available:
            return _fallback_answer(question, sources)

        if self.config.backend in {"llama_cpp", "auto"} and self._llm is None:
            try:
                from llama_cpp import Llama
            except ModuleNotFoundError:
                ollama_answer = self._answer_with_ollama(question, sources, system_prompt)
                return ollama_answer or _dependency_fallback_answer(question, sources)

            try:
                self._llm = Llama(
                    model_path=str(self.model_path),
                    n_ctx=self.config.n_ctx,
                    n_threads=self.config.n_threads,
                    verbose=False,
                )
            except Exception as exc:
                ollama_answer = self._answer_with_ollama(question, sources, system_prompt)
                return ollama_answer or _runtime_fallback_answer(question, sources, exc)

        prompt = _build_prompt(question, sources, system_prompt)
        response = self._llm(
            prompt,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            stop=["</s>", "用户：", "Question:"],
        )
        return response["choices"][0]["text"].strip()

    def _answer_with_ollama(
        self,
        question: str,
        sources: list[RetrievedChunk],
        system_prompt: str | None = None,
    ) -> str | None:
        prompt = _build_prompt(question, sources, system_prompt)
        payload = {
            "model": self.config.ollama_model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": self.config.keep_alive,
            "options": {
                "temperature": self.config.temperature,
                "num_predict": self.config.max_tokens,
                "num_ctx": self.config.n_ctx,
                "num_thread": self.config.n_threads,
                "num_batch": self.config.num_batch,
            },
        }
        request = urllib.request.Request(
            f"{self.config.ollama_url.rstrip('/')}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return None
        answer = str(data.get("response", "")).strip()
        return _strip_thinking(answer) if answer else None


def _build_prompt(
    question: str,
    sources: list[RetrievedChunk],
    system_prompt: str | None = None,
) -> str:
    context = "\n\n".join(
        f"[资料{i}] 标题：{source.title}\n来源：{source.location}\n{_trim_source_text(source.text)}"
        for i, source in enumerate(sources, start=1)
    )
    instruction = system_prompt.strip() if system_prompt and system_prompt.strip() else """你是企业内部知识库助手。请只根据给定资料回答问题。
如果资料中没有答案，请明确说“资料中未找到相关信息”，不要编造。
回答要简洁、准确，并在关键结论后标注资料编号。"""
    return f"""{instruction}

硬性规则，必须遵守：
1. 只能使用“资料”中的原文信息作答，禁止根据常识补充。
2. 如果回答涉及商品列表，每个商品必须包含以下字段，字段名不能省略：品牌、型号/规格、下单流程、权益、商品链接、限制说明。
3. 某个字段在资料中没有明确信息时，必须写“资料中未找到”，不能猜测。
4. 禁止输出“特色”“适合家庭/高端市场/小空间”等资料中没有的推荐理由。
5. 品牌名、型号名、链接必须逐字使用资料原文，禁止把“美的”改成“美尔”等相似词。
6. 每个商品标题后必须标注资料编号，例如“东芝真空沁米炊鲜饭煲RC-10ZWSC（资料2）”。
7. 最多列出 5 个商品。
8. 不要输出思考过程、推理过程、分析过程或 <think> 标签；只输出最终答案。

资料：
{context}

问题：{question}

答案："""


def _trim_source_text(text: str, max_chars: int = 1200) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[后续内容已截断]"


def _fallback_answer(question: str, sources: list[RetrievedChunk]) -> str:
    if not sources:
        return "没有检索到相关资料。"
    lines = [
        "当前未配置本地 GGUF 大模型，因此先返回检索到的候选资料。",
        f"问题：{question}",
        "",
    ]
    for i, source in enumerate(sources, start=1):
        preview = source.text[:300].replace("\n", " ")
        lines.append(f"[资料{i}] {source.location}：{preview}")
    return "\n".join(lines)


def _dependency_fallback_answer(question: str, sources: list[RetrievedChunk]) -> str:
    prefix = (
        "已检测到 GGUF 模型文件，但当前 Python 环境缺少 llama-cpp-python，"
        "所以暂时只返回检索到的候选资料。请运行：pip install llama-cpp-python"
    )
    return prefix + "\n\n" + _fallback_answer(question, sources)


def _runtime_fallback_answer(question: str, sources: list[RetrievedChunk], exc: Exception) -> str:
    prefix = (
        "已检测到 GGUF 模型和 llama-cpp-python，但当前环境加载模型失败，"
        f"错误：{exc}。暂时先返回检索到的候选资料。"
    )
    return prefix + "\n\n" + _fallback_answer(question, sources)


def _strip_thinking(answer: str) -> str:
    answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL | re.IGNORECASE)
    answer = re.sub(r"<think>.*", "", answer, flags=re.DOTALL | re.IGNORECASE)
    answer = re.sub(r"^\s*(思考过程|推理过程|分析过程)[:：].*?(?=\n\s*(答案|根据资料|1\.|一、)|\Z)", "", answer, flags=re.DOTALL)
    return answer.strip()


def strip_thinking(answer: str) -> str:
    return _strip_thinking(answer)
