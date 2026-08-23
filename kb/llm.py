"""LLM 接入层：Ollama 探测、模式解析（local/auto/cloud）、护栏参数、本地/云端双客户端统一接口。"""
from enum import Enum

import httpx

from kb.config import Settings


class LLMError(Exception):
    """LLM 调用异常（service 层据此转 503 或降级）。"""


class LLMStatus(str, Enum):
    """LLM 可用状态。"""

    LOCAL = "local"
    CLOUD = "cloud"
    DISABLED = "disabled"


class LLMClient:
    """本地（Ollama 原生 /api/chat）与云端（DeepSeek，openai SDK）统一生成接口。

    护栏参数硬编码默认值：think=False、temperature 0.2、num_ctx 4096、
    max_tokens 800（可被 chat 入参覆盖），不新增配置项。
    """

    def __init__(self, settings: Settings, http_client: httpx.Client | None = None):
        """http_client 注入用于测试；默认自建（base_url=settings.ollama_base_url）。
        构造时立即探测（每次构造重新探测，不使用模块级缓存）。"""
        self._settings = settings
        # 生成可能较慢（含模型冷加载），默认客户端给足超时；probe 另有 2s 限时
        self._http = http_client or httpx.Client(
            base_url=settings.ollama_base_url, timeout=120.0)
        self._local_ok = False
        self._cloud_ok = False
        self.probe()

    def probe(self) -> dict:
        """GET {ollama_base_url}/v1/models（2s 超时）→ 本地可用性；
        deepseek_api_key 非空 → 云端可用性。返回 {"local": bool, "cloud": bool}。"""
        local_ok = False
        try:
            resp = self._http.get("/v1/models", timeout=2.0)
            local_ok = resp.is_success
        except httpx.HTTPError:
            local_ok = False
        self._local_ok = local_ok
        self._cloud_ok = bool(self._settings.deepseek_api_key)
        return {"local": self._local_ok, "cloud": self._cloud_ok}

    @property
    def status(self) -> LLMStatus:
        """local 模式：本地可用→LOCAL 否则 DISABLED；
        cloud 模式：有 Key→CLOUD 否则 DISABLED；
        auto 模式：本地可用→LOCAL；无本地有云→CLOUD；都无→DISABLED。"""
        mode = self._settings.llm_mode
        if mode == "local":
            return LLMStatus.LOCAL if self._local_ok else LLMStatus.DISABLED
        if mode == "cloud":
            return LLMStatus.CLOUD if self._cloud_ok else LLMStatus.DISABLED
        # auto（默认）：本地优先，云端兜底
        if self._local_ok:
            return LLMStatus.LOCAL
        if self._cloud_ok:
            return LLMStatus.CLOUD
        return LLMStatus.DISABLED

    def chat(self, messages: list[dict], max_tokens: int | None = None,
             prefer: str = "auto") -> str:
        """统一生成接口。prefer: local/cloud/auto（路由用）。

        - DISABLED 状态直接抛 LLMError；
        - 显式 prefer 指定的后端不可用时抛 LLMError（不跨端回退，
          避免 local 模式或敏感场景下悄悄走云）；
        - auto：本地优先，云端兜底，都无抛 LLMError。
        """
        if self.status is LLMStatus.DISABLED:
            raise LLMError("LLM 不可用：本地 Ollama 未响应且未配置 DeepSeek API Key")
        mode = self._settings.llm_mode
        # 模式约束后端范围：local 模式禁云端，cloud 模式禁本地
        local_usable = self._local_ok and mode != "cloud"
        cloud_usable = self._cloud_ok and mode != "local"
        if prefer == "local":
            if not local_usable:
                raise LLMError("本地 LLM 不可用（Ollama 未响应或 cloud 模式禁用本地）")
            return self._chat_local(messages, max_tokens)
        if prefer == "cloud":
            if not cloud_usable:
                raise LLMError("云端 LLM 不可用（未配置 API Key 或 local 模式禁用云端）")
            return self._chat_cloud(messages, max_tokens)
        # prefer == "auto"：本地优先，云端兜底
        if local_usable:
            return self._chat_local(messages, max_tokens)
        return self._chat_cloud(messages, max_tokens)

    def _chat_local(self, messages: list[dict], max_tokens: int | None) -> str:
        """本地 Ollama 原生 /api/chat（openai SDK 不透传 think 参数，故直用 httpx）。"""
        body = {
            "model": self._settings.llm_model,
            "messages": messages,
            "think": False,
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_ctx": 4096,
                "max_tokens": max_tokens or 800,
            },
        }
        try:
            resp = self._http.post("/api/chat", json=body)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise LLMError(f"本地 LLM 调用失败: {exc}") from exc
        return data.get("message", {}).get("content") or ""

    def _chat_cloud(self, messages: list[dict], max_tokens: int | None) -> str:
        """云端 DeepSeek（openai SDK 兼容接口；延迟导入，纯本地模式无需安装 SDK）。"""
        try:
            from openai import OpenAI, OpenAIError
        except ImportError as exc:
            raise LLMError("未安装 openai SDK，无法调用云端 LLM") from exc
        client = OpenAI(api_key=self._settings.deepseek_api_key,
                        base_url=self._settings.deepseek_base_url)
        try:
            resp = client.chat.completions.create(
                model=self._settings.deepseek_model,
                messages=messages,
                temperature=0.2,
                max_tokens=max_tokens or 800,
            )
        except OpenAIError as exc:
            raise LLMError(f"云端 LLM 调用失败: {exc}") from exc
        return resp.choices[0].message.content or ""
