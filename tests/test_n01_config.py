from pathlib import Path


def test_默认配置(env_isolated):
    from kb.config import Settings
    # _env_file=None：禁用磁盘 .env（本地开发可能设了 KB_LLM_MODEL 降级值），
    # 本测试断言代码默认值，须与本地配置完全隔离
    s = Settings(_env_file=None)
    assert s.llm_mode == "auto"
    assert s.llm_model == "qwen3:4b"
    assert s.llm_temperature == 0.2
    assert s.llm_max_tokens == 800
    assert s.context_token_limit == 2000
    assert s.chroma_dir == Path(env_isolated) / "chroma"
    assert s.runtime_file == Path(env_isolated) / "runtime.json"
    assert s.sensitive_ns_list == []


def test_环境变量覆盖(env_isolated, monkeypatch):
    from kb.config import Settings
    monkeypatch.setenv("KB_LLM_MODE", "local")
    monkeypatch.setenv("KB_LLM_MODEL", "qwen3:1.7b")
    monkeypatch.setenv("KB_SENSITIVE_NAMESPACES", "private,diary")
    s = Settings()
    assert s.llm_mode == "local"
    assert s.llm_model == "qwen3:1.7b"
    assert s.sensitive_ns_list == ["private", "diary"]


def test_单例与环境模板(env_isolated):
    from kb.config import get_settings
    assert get_settings() is get_settings()
    example = Path(".env.example")
    assert example.exists()
    for line in example.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            # 模板只允许「键=空值」或注释，禁止出现真实密钥值
            key, _, value = line.partition("=")
            assert key.startswith("KB_") and value == "", f".env.example 含非空值: {line}"