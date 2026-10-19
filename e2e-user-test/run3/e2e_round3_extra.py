"""kb 第三轮补充 E2E：本轮修复的 UX 契约 + 隔离实例验证（用户视角）。

覆盖：
  A. 已启动服务（8021）：healthz 嵌入状态、空白查询 422、本地网页入库/重复替换/
     按 URL 删除、本地文件重复替换/删除。
  B. 鉴权实例（8022）：匿名探活放行、匿名业务 401、Bearer/X-API-Key 放行。
  C. 坏模型实例（8023）：启动不崩、healthz 暴露 embedder 状态、写入返回结构化 503。
  D. CLI：info/add/search/stats/audit/forget --dry-run/dedup --dry-run/eval 隔离。
  E. MCP stdio：initialize + tools/list（8 个工具）。
  F. skill 包构建：zip 根目录 SKILL.md、无 orchestra/测试/密钥。

只用标准库；子实例全部使用独立数据/日志目录，互不污染。
用法：venv\\Scripts\\python.exe e2e_round3_extra.py
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

RUN = Path(__file__).resolve().parent
REPO = RUN.parent.parent
PY = sys.executable
BASE = "http://127.0.0.1:8021"
WEB = "http://127.0.0.1:8030"
RESULTS: list[dict] = []


def rec(name: str, ok: bool, detail: str = "", extra=None) -> None:
    RESULTS.append({"item": name, "ok": ok, "detail": detail, "extra": extra})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


def http(base: str, method: str, path: str, body=None, headers=None,
         timeout: int = 90, raw: bool = False):
    data = None
    hdrs = {"Accept": "application/json, text/event-stream"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json; charset=utf-8"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            status, rh = resp.status, dict(resp.headers)
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        status, rh = e.code, dict(e.headers)
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}", {}
    if raw:
        return status, text, rh
    try:
        return status, json.loads(text), rh
    except json.JSONDecodeError:
        return status, text, rh


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def clean_env(tag: str, extra: dict | None = None) -> dict:
    """从当前环境剔除所有 KB_* 与 HF_*，再注入隔离配置。"""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("KB_") and not k.startswith("HF_")}
    base = RUN / "isolation" / tag
    env.update({
        "KB_API_HOST": "127.0.0.1",
        "KB_DATA_DIR": str(base / "data"),
        "KB_LOG_DIR": str(base / "logs"),
        "KB_WATCH_DIR": str(base / "watch"),
        "KB_DEVICE": "cpu",
        "KB_LLM_MODE": "off",
        # 显式关闭治理，隔离仓库 .env 的用户配置，保证可重复运行
        "KB_DEDUP_ENABLED": "false",
        "KB_DECAY_ENABLED": "false",
        "KB_FRESHNESS_ENABLED": "false",
        "KB_CONSOLIDATION_ENABLED": "false",
        "KB_ACCESS_AUDIT_ENABLED": "false",
        "HF_HUB_OFFLINE": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    (base / "watch").mkdir(parents=True, exist_ok=True)
    if extra:
        env.update(extra)
    return env


def start_instance(tag: str, port: int, extra_env: dict | None = None):
    """启动隔离实例，返回 (proc, log_path)；失败返回 (None, log_path)。"""
    base = RUN / "isolation" / tag
    base.mkdir(parents=True, exist_ok=True)
    out = base / "serve.out"
    err = base / "serve.err"
    env = clean_env(tag, {"KB_API_PORT": str(port), **(extra_env or {})})
    proc = subprocess.Popen(
        [PY, "-m", "kb", "serve"],
        cwd=str(base), env=env,
        stdout=out.open("w", encoding="utf-8"),
        stderr=err.open("w", encoding="utf-8"),
        stdin=subprocess.DEVNULL,
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            return None, err
        code, _, _ = http(f"http://127.0.0.1:{port}", "GET", "/api/v1/healthz", timeout=2)
        if code == 200:
            return proc, err
        time.sleep(1)
    proc.terminate()
    return None, err


def stop_instance(proc) -> None:
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


# ---------- A. 已启动主服务 ----------

def section_a() -> None:
    code, hz, _ = http(BASE, "GET", "/api/v1/healthz")
    emb = (hz or {}).get("embedder", {}) if isinstance(hz, dict) else {}
    rec("A1 healthz 暴露 embedder 状态且模型已就绪",
        code == 200 and emb.get("state") == "ok",
        f"status={code} state={emb.get('state')} model={emb.get('model')}",
        emb)

    code, body, _ = http(BASE, "POST", "/api/v1/search", {"query": "   "})
    rec("A2 空白检索返回 422（不再静默全库匹配）", code == 422,
        f"status={code}")

    code, body, _ = http(BASE, "POST", "/api/v1/ask", {"question": "\t\n "})
    rec("A3 空白提问返回 422", code == 422, f"status={code}")

    url = f"{WEB}/site_page.html"
    code, first, _ = http(BASE, "POST", "/api/v1/ingest/web", {"url": url},
                          timeout=120)
    ok_first = code == 200 and isinstance(first, dict) and first.get("chunks", 0) > 0
    rec("A4 本地网页入库成功", ok_first,
        f"status={code} source={first.get('source') if isinstance(first, dict) else first} "
        f"chunks={first.get('chunks') if isinstance(first, dict) else '-'}")

    code2, second, _ = http(BASE, "POST", "/api/v1/ingest/web", {"url": url},
                            timeout=120)
    ok_second = (code2 == 200 and isinstance(second, dict)
                 and second.get("replaced", 0) > 0
                 and second.get("chunks") == first.get("chunks"))
    rec("A5 同 URL 重复入库为原子替换（replaced>0，块数一致）", ok_second,
        f"status={code2} replaced={second.get('replaced') if isinstance(second, dict) else '-'} "
        f"chunks={second.get('chunks') if isinstance(second, dict) else '-'}")

    code, docs, _ = http(BASE, "GET", "/api/v1/documents")
    items = docs.get("items", []) if isinstance(docs, dict) else []
    same = [d for d in items if d.get("source") == url]
    rec("A6 文档列表按 source 聚合无重复条目",
        code == 200 and len(same) == 1 and same[0].get("chunks") == first.get("chunks"),
        f"status={code} matches={len(same)} chunks={same[0].get('chunks') if same else '-'}")

    code, hits, _ = http(BASE, "POST", "/api/v1/search",
                         {"query": "网页抓取 正文提取", "top_k": 5})
    found = any(h.get("source") == url for h in (hits.get("results", [])
                                                 if isinstance(hits, dict) else []))
    rec("A7 网页内容可被检索命中", code == 200 and found, f"status={code} found={found}")

    q = urllib.parse.urlencode({"source": url})
    code, dele, _ = http(BASE, "DELETE", f"/api/v1/documents?{q}")
    ok_del = code == 200 and isinstance(dele, dict) and dele.get("deleted", 0) > 0
    rec("A8 按 URL 查询参数删除成功", ok_del,
        f"status={code} deleted={dele.get('deleted') if isinstance(dele, dict) else '-'}")

    code, docs2, _ = http(BASE, "GET", "/api/v1/documents")
    still = [d for d in (docs2.get("items", []) if isinstance(docs2, dict) else [])
             if d.get("source") == url]
    rec("A9 删除后文档列表不再包含该 URL", code == 200 and not still,
        f"status={code} remaining={len(still)}")

    # 本地文件重复替换
    doc = RUN / "tmp" / "dup_doc.md"
    phrase = "星轨折跃-9c41"
    doc.write_text(f"# 替换测试\n\n唯一短语：{phrase}。这段内容用于验证重复导入是替换而非追加。\n",
                   encoding="utf-8")
    code1, r1, _ = http(BASE, "POST", "/api/v1/documents", {"path": str(doc)})
    code2, r2, _ = http(BASE, "POST", "/api/v1/documents", {"path": str(doc)})
    ok = (code1 == 200 and code2 == 200 and isinstance(r1, dict) and isinstance(r2, dict)
          and r1.get("replaced") == 0 and r2.get("replaced", 0) > 0
          and r1.get("chunks") == r2.get("chunks"))
    rec("A10 本地文件重复导入为替换而非追加", ok,
        f"first(replaced={r1.get('replaced') if isinstance(r1, dict) else '-'},"
        f"chunks={r1.get('chunks') if isinstance(r1, dict) else '-'}) "
        f"second(replaced={r2.get('replaced') if isinstance(r2, dict) else '-'},"
        f"chunks={r2.get('chunks') if isinstance(r2, dict) else '-'})")

    code, hits, _ = http(BASE, "POST", "/api/v1/search", {"query": phrase, "top_k": 5})
    srcs = [h.get("source") for h in (hits.get("results", [])
                                      if isinstance(hits, dict) else [])]
    rec("A11 替换后的唯一短语可检索（无内容丢失）", code == 200 and "dup_doc.md" in srcs,
        f"status={code} sources={srcs[:5]}")

    q = urllib.parse.urlencode({"source": "dup_doc.md"})
    code, dele, _ = http(BASE, "DELETE", f"/api/v1/documents?{q}")
    rec("A12 本地文件按 source 删除成功",
        code == 200 and isinstance(dele, dict) and dele.get("deleted", 0) > 0,
        f"deleted={dele.get('deleted') if isinstance(dele, dict) else '-'}")


# ---------- B. 鉴权实例 ----------

def section_b() -> None:
    if not port_free(8022):
        rec("B0 鉴权实例端口可用", False, "8022 已被占用，跳过")
        return
    proc, err = start_instance("auth", 8022, {"KB_API_KEY": "e2e-secret-key"})
    if proc is None:
        tail = err.read_text(encoding="utf-8", errors="replace")[-800:] if err.exists() else ""
        rec("B0 鉴权实例启动", False, f"启动失败：{tail}")
        return
    try:
        a = "http://127.0.0.1:8022"
        code, _, _ = http(a, "GET", "/api/v1/healthz")
        rec("B1 匿名健康探针放行（healthz 200）", code == 200, f"status={code}")

        code, body, _ = http(a, "POST", "/api/v1/memories",
                             {"content": "匿名写入应被拒绝"})
        ok = code == 401 and isinstance(body, dict) and body.get("error") == "UNAUTHORIZED"
        rec("B2 匿名业务请求返回 401", ok, f"status={code} error={body.get('error') if isinstance(body, dict) else body}")

        code, body, _ = http(a, "POST", "/api/v1/memories",
                             {"content": "带 Bearer 的写入"},
                             {"Authorization": "Bearer e2e-secret-key"})
        rec("B3 Bearer 正确密钥放行", code == 200, f"status={code}")

        code, body, _ = http(a, "GET", "/api/v1/memories",
                             headers={"X-API-Key": "e2e-secret-key"})
        rec("B4 X-API-Key 正确密钥放行", code == 200, f"status={code}")

        code, body, _ = http(a, "POST", "/api/v1/memories",
                             {"content": "错误密钥"}, {"Authorization": "Bearer wrong"})
        rec("B5 错误密钥返回 401", code == 401, f"status={code}")

        code, _, _ = http(a, "GET", "/")
        rec("B6 首页不豁免鉴权（401，防信息暴露）", code == 401, f"status={code}")
    finally:
        stop_instance(proc)


# ---------- C. 坏模型实例 ----------

def section_c() -> None:
    if not port_free(8023):
        rec("C0 坏模型实例端口可用", False, "8023 已被占用，跳过")
        return
    proc, err = start_instance(
        "badmodel", 8023,
        {"KB_EMBED_MODEL": "BAAI/kb-e2e-model-does-not-exist"})
    if proc is None:
        rec("C0 坏模型实例仍能启动（懒加载）", False,
            f"启动失败：{err.read_text(encoding='utf-8', errors='replace')[-600:] if err.exists() else ''}")
        return
    try:
        a = "http://127.0.0.1:8023"
        code, hz, _ = http(a, "GET", "/api/v1/healthz")
        emb = (hz or {}).get("embedder", {}) if isinstance(hz, dict) else {}
        rec("C1 坏模型配置下服务正常启动且 healthz 200",
            code == 200 and emb.get("state") in ("unloaded", "error"),
            f"status={code} state={emb.get('state')}", emb)

        code, body, _ = http(a, "POST", "/api/v1/memories",
                             {"content": "这条写入会触发模型加载失败"}, timeout=180)
        ok = (code == 503 and isinstance(body, dict)
              and body.get("error") == "EMBED_MODEL_UNAVAILABLE"
              and body.get("model") == "BAAI/kb-e2e-model-does-not-exist")
        rec("C2 嵌入模型不可用时写入返回结构化 503", ok,
            f"status={code} error={body.get('error') if isinstance(body, dict) else str(body)[:80]}")

        code, hz2, _ = http(a, "GET", "/api/v1/healthz")
        emb2 = (hz2 or {}).get("embedder", {}) if isinstance(hz2, dict) else {}
        rec("C3 失败后 healthz 标记 embedder error（可诊断）",
            code == 200 and emb2.get("state") == "error",
            f"state={emb2.get('state')} error={str(emb2.get('error'))[:60]}")
    finally:
        stop_instance(proc)


# ---------- D. CLI ----------

def run_cli(args: list[str], env: dict, timeout: int = 300):
    proc = subprocess.run([PY, "-m", "kb", *args], cwd=str(REPO), env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def section_d() -> None:
    env = clean_env("cli")
    phrase = "CLI-E2E-孤岛信标-77"
    code, out = run_cli(["info"], env)
    rec("D1 kb info 正常", code == 0, f"exit={code} out={out.strip()[:80]}")

    code, out = run_cli(["add", f"这是 CLI 端到端测试记忆：{phrase}，用于验证全链路。",
                         "--tags", "e2e,cli", "--client", "E2E", "--project", "run3"], env)
    rec("D2 kb add 写入成功", code == 0 and "已写入" in out,
        f"exit={code} out={out.strip()[:80]}")

    code, out = run_cli(["search", phrase], env)
    rec("D3 kb search 命中刚写入内容", code == 0 and phrase in out,
        f"exit={code} out={out.strip()[:100]}")

    code, out = run_cli(["stats"], env)
    rec("D4 kb stats 正常输出", code == 0 and "记忆库统计" in out,
        f"exit={code}")

    code, out = run_cli(["audit"], env)
    rec("D5 kb audit 正常输出", code == 0,
        f"exit={code} out={out.strip()[:80]}")

    code, out = run_cli(["forget", "--stale", "--dry-run"], env)
    rec("D6 kb forget --stale --dry-run 正常（默认不删除）", code == 0,
        f"exit={code} out={out.strip()[:80]}")

    code, out = run_cli(["dedup", "--dry-run"], env)
    rec("D7 kb dedup --dry-run 正常（记录不足2条时短路）", code == 0,
        f"exit={code} out={out.strip()[:80]}")

    # 显式开启 dedup/decay/freshness，eval 仍应成功（评测隔离）
    env_eval = dict(env)
    env_eval.update({"KB_DEDUP_ENABLED": "true", "KB_DECAY_ENABLED": "true",
                     "KB_FRESHNESS_ENABLED": "true"})
    code, out = run_cli(["eval"], env_eval, timeout=600)
    rec("D8 kb eval 不受用户治理开关影响（隔离临时库）", code == 0 and "kb eval 报告" in out,
        f"exit={code} tail={out.strip()[-120:]}")


# ---------- E. MCP stdio ----------

def section_e() -> None:
    env = clean_env("mcp_stdio")
    proc = subprocess.Popen([PY, "-m", "kb", "mcp"], cwd=str(REPO), env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    lines: queue.Queue[str] = queue.Queue()

    def reader():
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)

    threading.Thread(target=reader, daemon=True).start()

    def send(payload: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    def wait_for(mid: int, timeout: float = 60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = lines.get(timeout=1)
            except queue.Empty:
                if proc.poll() is not None:
                    return None
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("id") == mid:
                return obj
        return None

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "e2e-run3", "version": "0.1"}}})
        init = wait_for(1)
        info = init["result"].get("serverInfo", {}) if init and "result" in init else {}
        ok_init = info.get("name") == "kb" and bool(info.get("version"))
        rec("E1 MCP stdio initialize 成功且上报服务版本", bool(ok_init),
            f"server={info if ok_init else init}")

        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = wait_for(2)
        names = sorted(t["name"] for t in listed["result"]["tools"]) \
            if listed and "result" in listed else []
        expected = {"write_memory", "search_memory", "read_memory", "update_memory",
                    "delete_memory", "add_document", "add_webpage", "ask_kb"}
        rec("E2 MCP tools/list 返回 8 个约定工具", set(names) == expected,
            f"count={len(names)} missing={sorted(expected - set(names))} extra={sorted(set(names) - expected)}")
    finally:
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


# ---------- F. skill 包 ----------

def section_f() -> None:
    out_dir = RUN / "dist"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    proc = subprocess.run([PY, str(REPO / "scripts" / "build_skill_package.py"),
                           "--out-dir", str(out_dir)],
                          cwd=str(REPO), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    zips = list(out_dir.glob("*.zip")) if out_dir.exists() else []
    rec("F1 skill 包构建命令成功", proc.returncode == 0 and len(zips) == 1,
        f"exit={proc.returncode} out={proc.stdout.strip()[:100]} err={proc.stderr.strip()[:100]}")
    if not zips:
        return
    z = zips[0]
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        skill = zf.read("SKILL.md").decode("utf-8")
    required = {"SKILL.md", "README.md", "mcp.json", "env.example", "LICENSE"}
    flat = all("/" not in n for n in names)
    rec("F2 zip 根目录直接包含 SKILL.md 与配置模板",
        required.issubset(set(names)) and flat,
        f"files={names}")
    forbidden = [m for m in ("orchestra", "_archive", ".trae-html-share-packages",
                             "e2e-user-test") if m in skill]
    rec("F3 SKILL.md 为独立包（无 orchestra/归档/测试路径）且含标准 front matter",
        not forbidden and skill.startswith("---\n") and "name: kb-memory" in skill,
        f"forbidden={forbidden} size={len(skill)}B")


def main() -> int:
    print(f"== kb 第三轮补充 E2E ==  python={PY}")
    # 脚本可重复运行：清理上一轮隔离实例的数据/日志（主服务 8021 数据不动）
    for tag in ("auth", "badmodel", "cli", "mcp_stdio"):
        p = RUN / "isolation" / tag
        if p.exists():
            shutil.rmtree(p)
    for name, fn in (("A 主服务 UX 契约", section_a), ("B 鉴权实例", section_b),
                     ("C 坏模型容错", section_c), ("D CLI", section_d),
                     ("E MCP stdio", section_e), ("F skill 包", section_f)):
        print(f"\n--- {name} ---")
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            rec(f"{name} 执行异常", False, f"{type(e).__name__}: {e}")

    passed = sum(1 for r in RESULTS if r["ok"])
    total = len(RESULTS)
    print(f"\n== 结果：{passed}/{total} ==")
    out = RUN / "e2e_round3_result.json"
    out.write_text(json.dumps({"passed": passed, "total": total, "results": RESULTS},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"明细：{out}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
