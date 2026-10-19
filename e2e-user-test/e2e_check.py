"""kb 端到端功能测试（用户视角）——对本地已启动的 kb 服务做全功能探测。

用法：python e2e_check.py [--base http://127.0.0.1:8000]
输出：终端逐项结果 + 同目录 e2e_result.json

设计：只用标准库（urllib），不依赖被测项目的包，确保"测试脚本本身"不引入额外变量。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RESULTS: list[dict] = []
BASE = "http://127.0.0.1:8000"
WORK = Path(__file__).resolve().parent


def rec(name: str, ok: bool, detail: str = "", extra=None) -> None:
    """记录一项结果并打印。"""
    RESULTS.append({"item": name, "ok": ok, "detail": detail, "extra": extra})
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail else ""))


def http(method: str, path: str, body=None, headers=None, timeout=60, raw=False):
    """发一个 HTTP 请求，返回 (status, parsed_json_or_text, headers)。"""
    url = BASE + path
    data = None
    hdrs = {"Accept": "application/json, text/event-stream"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json; charset=utf-8"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            status = resp.status
            rh = dict(resp.headers)
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        status = e.code
        rh = dict(e.headers)
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}", {}
    if raw:
        return status, text, rh
    try:
        return status, json.loads(text), rh
    except json.JSONDecodeError:
        return status, text, rh


# ---------- MCP (streamable HTTP) ----------

def mcp_post(method: str, params: dict, session_id: str | None = None, msg_id: int = 1):
    """调 MCP JSON-RPC；返回 (status, payload, headers)。响应可能是 SSE，这里做解析。"""
    payload = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
    hdrs = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if session_id:
        hdrs["Mcp-Session-Id"] = session_id
    status, text, rh = http("POST", "/mcp/", body=payload, headers=hdrs, raw=True, timeout=90)
    if status == -1:
        return status, text, rh
    parsed = None
    t = text.strip()
    if t.startswith("event:") or "data:" in t:
        for line in t.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                try:
                    parsed = json.loads(chunk)
                    break
                except json.JSONDecodeError:
                    continue
    if parsed is None:
        try:
            parsed = json.loads(t)
        except json.JSONDecodeError:
            parsed = text
    return status, parsed, rh


def test_mcp() -> None:
    """MCP 端点：initialize → tools/list → tools/call。"""
    status, payload, hdrs = mcp_post("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "e2e-check", "version": "1.0"},
    })
    if status != 200 or not isinstance(payload, dict) or "result" not in payload:
        rec("MCP initialize", False, f"status={status} resp={str(payload)[:200]}")
        return
    server = payload["result"].get("serverInfo", {})
    rec("MCP initialize", True, f"serverInfo={server}")

    session_id = hdrs.get("mcp-session-id") or hdrs.get("Mcp-Session-Id")
    if not session_id:
        rec("MCP session 建立", False, "响应头无 mcp-session-id")
        return
    rec("MCP session 建立", True, f"session={session_id[:12]}…")

    # initialized 通知（无 id）
    notif = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    req = urllib.request.Request(
        BASE + "/mcp/", data=json.dumps(notif).encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "Mcp-Session-Id": session_id}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
    except Exception:  # noqa: BLE001
        pass

    status, payload, _ = mcp_post("tools/list", {}, session_id=session_id, msg_id=2)
    tools = []
    if status == 200 and isinstance(payload, dict) and "result" in payload:
        tools = [t["name"] for t in payload["result"].get("tools", [])]
    rec("MCP tools/list", bool(tools), f"{len(tools)} 个工具: {', '.join(tools)}")

    # 通过 MCP 调用 write_memory + search_memory（真正的 Agent 路径）
    status, payload, _ = mcp_post("tools/call", {
        "name": "write_memory",
        "arguments": {"content": "E2E 测试：MCP 通道写入验证 " + time.strftime("%H:%M:%S"),
                      "tags": ["e2e", "mcp"]},
    }, session_id=session_id, msg_id=3)
    ok = status == 200 and isinstance(payload, dict) and "result" in payload
    rec("MCP tools/call write_memory", ok, str(payload)[:200] if not ok else "")

    status, payload, _ = mcp_post("tools/call", {
        "name": "search_memory", "arguments": {"query": "MCP 通道写入验证", "top_k": 3},
    }, session_id=session_id, msg_id=4)
    ok = status == 200 and isinstance(payload, dict) and "result" in payload
    rec("MCP tools/call search_memory", ok, str(payload)[:300] if not ok else "")


# ---------- REST 全功能 ----------

def test_rest() -> None:
    # 1. 健康检查
    status, data, _ = http("GET", "/api/v1/healthz")
    ok = status == 200 and isinstance(data, dict) and data.get("status") == "ok"
    rec("GET /healthz", ok, f"status={status} body={str(data)[:160]}")

    # 2. 写入记忆
    status, data, _ = http("POST", "/api/v1/memories", {
        "content": "E2E 测试记忆：用户偏好深色主题，主力语言 Python",
        "tags": ["e2e", "偏好"],
        "namespace": "e2e_test",
    })
    mem_id = data.get("id") if isinstance(data, dict) else None
    rec("POST /memories 写入", status == 200 and bool(mem_id),
        f"status={status} id={mem_id} body={str(data)[:160]}")

    # 3. 列表
    status, data, _ = http("GET", "/api/v1/memories?limit=10")
    total = data.get("total") if isinstance(data, dict) else None
    rec("GET /memories 列表", status == 200, f"status={status} total={total}")

    # 4. 读取单条
    if mem_id:
        status, data, _ = http("GET", f"/api/v1/memories/{mem_id}")
        ok = status == 200 and isinstance(data, dict) and data.get("id") == mem_id
        rec("GET /memories/{id} 读取", ok, f"status={status} content={str(data.get('content'))[:60]}")

        # 5. 更新
        status, data, _ = http("PATCH", f"/api/v1/memories/{mem_id}",
                               {"content": "E2E 测试记忆（已更新）：用户偏好深色主题"})
        ok = status == 200 and isinstance(data, dict) and "已更新" in str(data.get("content", ""))
        rec("PATCH /memories/{id} 更新", ok, f"status={status}")

    # 6. 读取不存在的记录（错误路径）
    status, data, _ = http("GET", "/api/v1/memories/__nonexistent__")
    rec("GET /memories/{id} 不存在 → 404", status == 404, f"status={status}")

    # 7. 三种检索模式（响应契约：{"results": [...]}）
    for mode in ("hybrid", "vector", "keyword"):
        status, data, _ = http("POST", "/api/v1/search",
                               {"query": "用户界面主题偏好", "top_k": 5, "mode": mode})
        hits = data.get("results") if isinstance(data, dict) else None
        n = len(hits) if hits is not None else -1
        rec(f"POST /search mode={mode}", status == 200 and n > 0,
            f"status={status} 命中={n}")

    # 8. 文档入库（本地文件）
    doc = WORK / "e2e_sample_doc.md"
    doc.write_text("# E2E 样例文档\n\n本项目是一个本地优先的 Agent 记忆与知识服务，"
                   "采用向量检索与 BM25 关键词检索的混合方案，并用 RRF 融合排序。\n"
                   "支持 Markdown、PDF、DOCX、TXT 等格式。\n", encoding="utf-8")
    status, data, _ = http("POST", "/api/v1/documents", {"path": str(doc)})
    ok = status == 200 and isinstance(data, dict) and "chunks" in data
    rec("POST /documents 文档入库", ok, f"status={status} body={str(data)[:160]}")

    # 9. 文档列表
    status, data, _ = http("GET", "/api/v1/documents")
    rec("GET /documents 列表", status == 200, f"status={status} body={str(data)[:160]}")

    # 10. 入库后是否可被检索到（端到端价值验证）
    status, data, _ = http("POST", "/api/v1/search",
                           {"query": "RRF 融合排序 混合检索方案", "top_k": 5})
    hit_txt = json.dumps(data, ensure_ascii=False)
    rec("入库文档可被检索", status == 200 and "RRF" in hit_txt,
        f"status={status} 命中内容含'RRF'={'RRF' in hit_txt}")

    # 11. 网页入库（需外网）
    status, data, _ = http("POST", "/api/v1/ingest/web", {"url": "https://example.com"}, timeout=90)
    rec("POST /ingest/web 网页入库", status == 200,
        f"status={status} body={str(data)[:200]}")

    # 12. RAG 问答（无 LLM 预期 503）
    status, data, _ = http("POST", "/api/v1/ask", {"question": "这个项目用什么检索方案？"}, timeout=120)
    llm_disabled = status == 503
    rec("POST /ask 无LLM降级", status in (200, 503),
        f"status={status} body={str(data)[:200]}"
        + ("（预期 503：默认 KB_LLM_MODE=off）" if llm_disabled else ""))

    # 13. 治理端点
    status, data, _ = http("GET", "/api/v1/governance/stats")
    ok = status == 200 and isinstance(data, dict) and "total_count" in data
    rec("GET /governance/stats", ok, f"status={status} body={str(data)[:180]}")

    status, data, _ = http("GET", "/api/v1/governance/config")
    ok = status == 200 and isinstance(data, dict) and "decay_enabled" in data
    rec("GET /governance/config", ok, f"status={status} body={str(data)[:180]}")

    # 14. 日志端点
    status, data, _ = http("GET", "/api/v1/logs?limit=5")
    ok = status == 200 and isinstance(data, dict) and "items" in data
    rec("GET /logs", ok, f"status={status} total={data.get('total') if isinstance(data,dict) else '-'}")

    status, data, _ = http("GET", "/api/v1/logs/events")
    ok = status == 200 and isinstance(data, dict)
    rec("GET /logs/events", ok, f"status={status} keys={list(data)[:5] if isinstance(data,dict) else '-'}")

    # 15. 审计端点（契约：client 或 agent 至少提供一个）
    status, data, _ = http("GET", "/api/v1/audit?client=CLI")
    ok = status == 200 and isinstance(data, dict) and "items" in data
    rec("GET /audit?client=CLI", ok,
        f"status={status} total={data.get('total') if isinstance(data,dict) else '-'}")
    status, data, _ = http("GET", "/api/v1/audit")
    rec("GET /audit 缺参数 → 422", status == 422,
        f"status={status} {str(data)[:120]}")

    # 16. 看板（HTML）
    status, data, _ = http("GET", "/dashboard/", raw=True)
    ok = status == 200 and "<html" in str(data).lower()
    rec("GET /dashboard 看板", ok, f"status={status}")

    # 17. 根导航页
    status, data, _ = http("GET", "/", raw=True)
    rec("GET / 根页面", status == 200, f"status={status}")

    # 18. 删除（清理）
    if mem_id:
        status, data, _ = http("DELETE", f"/api/v1/memories/{mem_id}")
        rec("DELETE /memories/{id}", status == 200, f"status={status} body={str(data)[:120]}")


def main() -> int:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base

    print("=" * 72)
    print(f"kb 端到端功能测试  目标={BASE}")
    print("=" * 72)

    t0 = time.time()
    try:
        test_rest()
    except Exception as e:  # noqa: BLE001
        rec("REST 测试块异常", False, f"{type(e).__name__}: {e}")
    try:
        test_mcp()
    except Exception as e:  # noqa: BLE001
        rec("MCP 测试块异常", False, f"{type(e).__name__}: {e}")

    passed = sum(1 for r in RESULTS if r["ok"])
    total = len(RESULTS)
    print("-" * 72)
    print(f"合计：{passed}/{total} 通过，耗时 {time.time()-t0:.1f}s")
    (WORK / "e2e_result.json").write_text(
        json.dumps({"passed": passed, "total": total, "items": RESULTS},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
