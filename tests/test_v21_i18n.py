"""v2.1 语言与自定义规则验收测试（spec §5）。"""
from pathlib import Path

import pytest


@pytest.fixture
def settings(env_isolated):
    from kb.config import get_settings
    get_settings.cache_clear()
    return get_settings()


def test_默认配置(settings):
    assert settings.memory_lang == "auto"
    assert settings.rules_max_chars == 4000
    assert settings.project_cache_size == 4
    assert settings.idle_unload_seconds == 600


def test_记忆语言_显式指定(monkeypatch, settings):
    from kb import i18n
    monkeypatch.setenv("KB_MEMORY_LANG", "en")
    from kb.config import get_settings
    get_settings.cache_clear()
    assert i18n.memory_lang() == "English"
    monkeypatch.setenv("KB_MEMORY_LANG", "zh")
    get_settings.cache_clear()
    assert i18n.memory_lang().startswith("Chinese")
    monkeypatch.setenv("KB_MEMORY_LANG", "fr")
    get_settings.cache_clear()
    assert i18n.memory_lang().startswith("French")


def test_记忆语言_auto跟随系统(settings, monkeypatch):
    from kb import i18n
    monkeypatch.setattr(i18n, "system_language_code", lambda: "ja-JP")
    assert i18n.memory_lang().startswith("Japanese")
    monkeypatch.setattr(i18n, "system_language_code", lambda: "xx-YY")
    assert i18n.memory_lang() == "xx-YY"


def test_自定义规则_顺序与截断(settings, tmp_path, monkeypatch):
    from kb import i18n
    fake_home = tmp_path / "home"
    (fake_home / ".kb").mkdir(parents=True)
    (fake_home / ".kb" / "rules.md").write_text("全局规则：偏好中文", encoding="utf-8")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.rules_file.write_text("本安装规则：不写密钥", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    rules = i18n.custom_rules()
    assert rules.index("全局规则") < rules.index("本安装规则")
    monkeypatch.setenv("KB_RULES_MAX_CHARS", "5")
    from kb.config import get_settings
    get_settings.cache_clear()
    assert len(i18n.custom_rules()) == 5


def test_instructions注入语言与规则(settings, tmp_path, monkeypatch):
    from kb import i18n
    fake_home = tmp_path / "home2"
    (fake_home / ".kb").mkdir(parents=True)
    (fake_home / ".kb" / "rules.md").write_text("只在用户说记住时写入", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("KB_LANG", "zh")
    monkeypatch.setenv("KB_MEMORY_LANG", "en")
    from kb.config import get_settings
    get_settings.cache_clear()
    text = i18n.mcp_instructions()
    assert "English" in text
    assert "只在用户说记住时写入" in text
    assert "用户自定义记忆规则" in text
    # 无规则时不出现空标题
    (fake_home / ".kb" / "rules.md").unlink()
    assert "用户自定义记忆规则" not in i18n.mcp_instructions()
