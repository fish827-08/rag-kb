"""TASK-0055：B3 关联窗口（spec §4.4）纯函数与命令测试。

验收：test_关联窗口超限归档（第6张关联触发最早关联归档为摘要并解除关联）、
test_归档摘要格式复用b3_summary；均绿；全量 orchestra/tests/ 回归绿。
"""
import pytest

from b3 import check_summary_tags
from relation import (MAX_WINDOW, add_related, archive_relation_summary,
                      cmd_relation_add, cmd_relation_list, cmd_relation_remove,
                      parse_relation, remove_related, render_relation)


# ---- 纯函数测试 ----
def test_parse_relation():
    content = "RELATION TASK-0046 -> TASK-0035,TASK-0036\n关联数：2（≤5）"
    p = parse_relation(content)
    assert p["task_id"] == "TASK-0046"
    assert p["related"] == ["TASK-0035", "TASK-0036"]
    assert p["count"] == 2


def test_parse_relation空关联():
    content = "RELATION TASK-0046 -> \n关联数：0（≤5）"
    p = parse_relation(content)
    assert p["related"] == []
    assert p["count"] == 0


def test_parse_relation格式错误抛异常():
    with pytest.raises(ValueError):
        parse_relation("坏内容")


def test_render_relation():
    content = render_relation("TASK-0046", ["TASK-0035", "TASK-0036"])
    assert content.startswith("RELATION TASK-0046 -> TASK-0035,TASK-0036")
    assert "关联数：2" in content


def test_add_related不超窗口():
    base = render_relation("TASK-0046", ["TASK-0035"])
    new_content, archived = add_related(base, "TASK-0036")
    assert archived is None
    p = parse_relation(new_content)
    assert p["related"] == ["TASK-0035", "TASK-0036"]


def test_add_related不重复():
    base = render_relation("TASK-0046", ["TASK-0035"])
    new_content, archived = add_related(base, "TASK-0035")
    assert archived is None
    assert new_content == base  # 已关联不变化


def test_关联窗口超限归档():
    """第6张关联触发最早关联归档为摘要并解除关联（spec §4.4）。"""
    # 已有 5 张关联（满窗口 MAX_WINDOW=5）
    related = [f"TASK-{i:04d}" for i in range(1, 6)]  # TASK-0001~0005
    base = render_relation("TASK-0046", related)
    new_content, archived = add_related(base, "TASK-0006")
    # 最早关联 TASK-0001 被归档
    assert archived == "TASK-0001"
    p = parse_relation(new_content)
    # 解除最早关联，加入新关联 → 仍 5 张
    assert p["count"] == MAX_WINDOW
    assert "TASK-0001" not in p["related"]
    assert "TASK-0006" in p["related"]
    assert p["related"] == ["TASK-0002", "TASK-0003", "TASK-0004",
                            "TASK-0005", "TASK-0006"]


def test_归档摘要格式复用b3_summary():
    """归档摘要通过 b3.check_summary_tags（四类保留标签齐全）。"""
    card_content = (
        "TASK-0001 done worker-1 | 测试卡标题\n"
        "目标：测试目标\n"
        "约束：测试约束\n"
        "验收：测试验收\n"
        "结果：测试结果"
    )
    summary = archive_relation_summary(card_content, "TASK-0001")
    # 首行格式：SUMMARY <task_id> relation-archive | 标题
    assert summary.startswith("SUMMARY TASK-0001 relation-archive | 测试卡标题")
    # 四类保留标签齐全（通过 b3 校验）
    missing = check_summary_tags(summary)
    assert missing == [], f"归档摘要缺失保留标签: {missing}"
    # 包含原卡关键信息
    assert "测试目标" in summary
    assert "测试验收" in summary


def test_归档摘要缺字段标无():
    """原卡缺失字段时归档摘要标'无'，仍通过 b3 校验。"""
    card_content = "TASK-0002 claimed worker-2 | 简卡\n"
    summary = archive_relation_summary(card_content, "TASK-0002")
    missing = check_summary_tags(summary)
    assert missing == []
    assert summary.startswith("SUMMARY TASK-0002 relation-archive | 简卡")


def test_remove_related():
    base = render_relation("TASK-0046", ["TASK-0035", "TASK-0036"])
    new_content = remove_related(base, "TASK-0035")
    p = parse_relation(new_content)
    assert p["related"] == ["TASK-0036"]
    assert p["count"] == 1


# ---- 命令函数测试（mock relation._request）----
def _patch_request(monkeypatch, responses):
    """patch relation._request，返回预置响应，记录调用。"""
    import relation
    calls = []

    def fake(method, path, body=None):
        calls.append((method, path, body))
        key = f"{method} {path}"
        if key in responses:
            return responses[key]
        raise AssertionError(f"未预置请求: {key}")

    monkeypatch.setattr(relation, "_request", fake)
    return calls


def test_cmd_relation_add新建记录(monkeypatch):
    responses = {
        "GET /memories?tag=relation&limit=1000": {"items": []},
        "POST /memories": {"id": "rec-1"},
    }
    calls = _patch_request(monkeypatch, responses)
    cmd_relation_add("TASK-0046", "TASK-0035")
    post_calls = [c for c in calls if c[0] == "POST"]
    assert len(post_calls) == 1
    assert "RELATION TASK-0046 -> TASK-0035" in post_calls[0][2]["content"]
    assert post_calls[0][2]["tags"] == ["relation"]


def test_cmd_relation_list无记录(monkeypatch, capsys):
    responses = {"GET /memories?tag=relation&limit=1000": {"items": []}}
    _patch_request(monkeypatch, responses)
    cmd_relation_list("TASK-0046")
    out = capsys.readouterr().out
    assert "无关联记录" in out


def test_cmd_relation_list有记录(monkeypatch, capsys):
    rel_rec = {"id": "rel-1",
               "content": render_relation("TASK-0046", ["TASK-0035", "TASK-0036"])}
    responses = {"GET /memories?tag=relation&limit=1000": {"items": [rel_rec]}}
    _patch_request(monkeypatch, responses)
    cmd_relation_list("TASK-0046")
    out = capsys.readouterr().out
    assert "TASK-0046 关联（2张）" in out
    assert "TASK-0035" in out and "TASK-0036" in out


def test_cmd_relation_remove(monkeypatch):
    rel_rec = {"id": "rel-1",
               "content": render_relation("TASK-0046", ["TASK-0035", "TASK-0036"])}
    responses = {
        "GET /memories?tag=relation&limit=1000": {"items": [rel_rec]},
        "PATCH /memories/rel-1": {},
    }
    calls = _patch_request(monkeypatch, responses)
    cmd_relation_remove("TASK-0046", "TASK-0035")
    patch_calls = [c for c in calls if c[0] == "PATCH"]
    assert len(patch_calls) == 1
    assert "TASK-0035" not in patch_calls[0][2]["content"]
    assert "TASK-0036" in patch_calls[0][2]["content"]


def test_cmd_relation_add超限自动归档(monkeypatch, capsys):
    """命令层：第6张关联触发最早关联归档 summary + 解除关联。"""
    related = [f"TASK-{i:04d}" for i in range(1, 6)]
    rel_rec = {"id": "rel-1", "content": render_relation("TASK-0046", related)}
    archived_card = {
        "id": "card-1",
        "content": "TASK-0001 done worker-1 | 最早卡\n目标：g\n约束：c\n验收：a\n结果：r",
    }
    responses = {
        "GET /memories?tag=relation&limit=1000": {"items": [rel_rec]},
        "GET /memories?tag=taskboard&limit=1000": {"items": [archived_card]},
        "POST /memories": {"id": "summary-1"},
        "PATCH /memories/rel-1": {},
    }
    calls = _patch_request(monkeypatch, responses)
    cmd_relation_add("TASK-0046", "TASK-0006")
    out = capsys.readouterr().out
    # 归档提示
    assert "TASK-0001" in out and "归档" in out
    # POST 生成 summary（tag=summary）
    post_calls = [c for c in calls if c[0] == "POST"]
    assert any(c[2]["tags"] == ["summary"] for c in post_calls)
    # PATCH 更新 relation（解除最早关联）
    patch_calls = [c for c in calls if c[0] == "PATCH"]
    assert len(patch_calls) == 1
    assert "TASK-0001" not in patch_calls[0][2]["content"]
    assert "TASK-0006" in patch_calls[0][2]["content"]
