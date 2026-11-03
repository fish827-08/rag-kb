"""N36 运行时兜底验收（2026-11-03）。

背景：runtime.json 可能残留 cuda 选择，而当前 torch 是 CPU 版。
若不兜底，模型会在 cuda 上失败后退回在线加载，触发 HuggingFace 网络重试，
一次查询卡住数分钟（实测 2026-11-03，bge-m3 + 无网）。
"""
import pytest

pytestmark = pytest.mark.integration


def test_cuda不可用时自动回退cpu(env_isolated):
    from kb.config import Settings, get_settings
    from kb.service import KBService

    get_settings.cache_clear()
    svc = KBService(Settings(device="cuda", llm_api_key="", deepseek_api_key=""))
    assert svc.device == "cpu"
