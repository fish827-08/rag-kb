"""v2.5.0 嵌入模型迁移端到端验收脚本（仅标准库，无额外依赖）。

用法：
    1. 启动服务：python -m kb serve
    2. 运行：python scripts/verify_model_migration.py --target BAAI/bge-m3

脚本会：
    - 把 samples/ 下的多种数据（记忆 / md / txt / html / json / docx / csv / 多 sheet xlsx）
      导入独立项目（默认 verify-migration）；
    - 在迁移前后执行同一组检索与表格问答断言；
    - 触发嵌入模型迁移并轮询进度，校验迁移后记录数不变、检索结果仍正确；
    - 可选 --delete-old 删除旧模型数据。

退出码：0=全部通过；1=有断言失败或迁移失败。
English: End-to-end acceptance script for the v2.5.0 embedding-model migration,
using only the standard library and a running kb service.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SAMPLES = ROOT / "samples"
PROJECT = "verify-migration"

PASS = "PASS"
FAIL = "FAIL"


def api(base: str, method: str, path: str, body=None, timeout: float = 300):
    """调用 REST 接口；非 2xx 抛 RuntimeError（带响应体，便于定位）。"""
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base.rstrip("/") + path, data=data,
                                 method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接服务 {base}：{exc.reason}") from exc


def wait_ready(base: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return api(base, "GET", "/api/v1/healthz", timeout=5)
        except RuntimeError:
            time.sleep(0.5)
    raise RuntimeError(f"服务未就绪：{base}（等待 {timeout:.0f}s）")


def import_memories(base: str, samples: Path, project: str) -> int:
    path = samples / "memories.json"
    items = json.loads(path.read_text(encoding="utf-8"))
    for item in items:
        api(base, "POST", "/api/v1/memories", {
            "content": item["content"],
            "tags": item.get("tags") or [],
            "agent_id": item.get("agent_id") or "TASK-VERIFY",
            "client": "verify-script",
            "project": project,
        })
    return len(items)


def import_files(base: str, samples: Path, project: str) -> list[dict]:
    """导入 samples/knowledge 与 samples/tables 下的全部样例文件。"""
    results = []
    files = []
    for folder in ("knowledge", "tables"):
        for path in sorted((samples / folder).iterdir()):
            if path.is_file() and not path.name.startswith("."):
                files.append(path)
    for path in files:
        resp = api(base, "POST", "/api/v1/documents", {
            "path": str(path), "project": project,
            "agent_id": "TASK-VERIFY", "client": "verify-script",
        })
        chunks = int(resp.get("chunks") or 0)
        results.append({"file": path.name, "chunks": chunks,
                        "table_chunks": int(resp.get("table_chunks") or 0)})
        if chunks <= 0:
            raise RuntimeError(f"样例文件未产生任何记录：{path.name}")
    return results


def search(base: str, query: str, project: str, mode: str = "hybrid") -> list[dict]:
    return api(base, "POST", "/api/v1/search", {
        "query": query, "top_k": 5, "mode": mode, "project": project,
        "agent_id": "TASK-VERIFY", "client": "verify-script",
    }).get("results") or []


def table_answer(base: str, question: str, project: str) -> dict:
    return api(base, "POST", "/api/v1/ask", {
        "question": question, "project": project,
        "agent_id": "TASK-VERIFY", "client": "verify-script",
    })


def active_records(base: str) -> int:
    """当前模型在所有作用域的记录总数（healthz 有 5 秒缓存，迁移校验不能用）。"""
    info = api(base, "GET", "/api/v1/embed")
    active = next((m for m in info.get("models") or [] if m.get("active")), {})
    return int(active.get("records") or 0)


def check(name: str, fn) -> dict:
    try:
        fn()
        return {"name": name, "result": PASS}
    except AssertionError as exc:
        return {"name": name, "result": FAIL, "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 - 脚本需要汇总所有失败项
        return {"name": name, "result": FAIL, "detail": f"{type(exc).__name__}: {exc}"}


def _any_hit(results: list[dict], needle: str) -> bool:
    return any(needle in (r.get("content") or "") for r in results)


def expected_checks(base: str, project: str) -> list[dict]:
    """迁移前后都要通过的一组断言（覆盖记忆 + 多种文档 + 表格）。"""
    checks = []

    def semantic():
        hits = search(base, "bge-m3 的向量维度是多少", project)
        assert hits, "语义检索无结果"
        assert _any_hit(hits, "1024"), f"未命中 1024：{[h.get('source') for h in hits]}"

    def keyword():
        hits = search(base, "迁移窗口只读", project, mode="keyword")
        assert hits, "关键词检索无结果"
        assert _any_hit(hits, "只读"), f"未命中只读：{[h.get('source') for h in hits]}"

    def memory():
        hits = search(base, "发布新版本前要做什么", project)
        assert hits, "记忆检索无结果"
        assert _any_hit(hits, "全量测试"), f"未命中全量测试：{[h.get('content') for h in hits]}"

    def docx():
        hits = search(base, "旧模型数据需要手动删除吗", project)
        assert any((h.get("source") or "").endswith("meeting-minutes.docx")
                   for h in hits), f"未命中 docx：{[(h.get('source'), h.get('content', '')[:40]) for h in hits]}"

    def html():
        hits = search(base, "v2.5.0 项目复盘", project)
        assert any((h.get("source") or "").endswith("retrospective.html")
                   for h in hits), f"未命中 html：{[h.get('source') for h in hits]}"

    def json_doc():
        # JSON 以原文入库，用字段名做关键词检索最稳定
        hits = search(base, "default_port default_embed_model 8000",
                      project, mode="keyword")
        assert any((h.get("source") or "").endswith("settings.json")
                   for h in hits), f"未命中 json：{[h.get('source') for h in hits]}"

    def csv_table():
        resp = table_answer(base, "一班王五的数学成绩", project)
        assert resp.get("table") is True, f"未走表格确定性路径：{resp}"
        assert "92" in resp.get("answer", ""), f"答案不含 92：{resp.get('answer')}"
        assert any((s.get("source") or "").endswith("student-scores.csv")
                   for s in resp.get("sources") or []), f"来源不对：{resp.get('sources')}"

    def xlsx_table():
        resp = table_answer(base, "华东仓 螺丝刀 数量", project)
        assert resp.get("table") is True, f"未走表格确定性路径：{resp}"
        assert "120" in resp.get("answer", ""), f"答案不含 120：{resp.get('answer')}"
        assert any((s.get("source") or "").endswith("inventory.xlsx")
                   for s in resp.get("sources") or []), f"来源不对：{resp.get('sources')}"

    for name, fn in (("语义检索（md/txt）", semantic),
                     ("关键词检索", keyword),
                     ("记忆检索（记忆）", memory),
                     ("DOCX 文档命中", docx),
                     ("HTML 文档命中", html),
                     ("JSON 文档命中", json_doc),
                     ("CSV 表格问答（同名消歧）", csv_table),
                     ("XLSX 多 sheet 问答（按表名）", xlsx_table)):
        checks.append(check(name, fn))
    return checks


def print_checks(checks: list[dict], prefix: str = "") -> int:
    failed = 0
    for item in checks:
        line = f"  [{item['result']}] {prefix}{item['name']}"
        if item.get("detail"):
            line += f" — {item['detail']}"
        print(line)
        failed += item["result"] == FAIL
    return failed


def migrate(base: str, target: str, timeout: float, poll: float) -> dict:
    print(f"\n[迁移] 触发迁移 → {target}")
    api(base, "POST", "/api/v1/embed/migrate", {"model": target})
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        info = api(base, "GET", "/api/v1/embed/migrate", timeout=10)
        snapshot = (info.get("status"), info.get("percent"), info.get("done"),
                    info.get("total"))
        if snapshot != last:
            print(f"  状态={snapshot[0]} 进度={snapshot[1]}% "
                  f"({snapshot[2]}/{snapshot[3]})")
            last = snapshot
        if info.get("status") in ("done", "failed"):
            return info
        time.sleep(max(0.2, poll))
    raise RuntimeError(f"迁移超时（>{timeout:.0f}s）")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="kb v2.5.0 模型迁移端到端验收（记忆 + 多种文档 + 表格）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--samples", default=str(DEFAULT_SAMPLES))
    parser.add_argument("--project", default=PROJECT)
    parser.add_argument("--target", default="BAAI/bge-m3")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument("--delete-old", action="store_true",
                        help="迁移成功后删除旧模型数据")
    parser.add_argument("--skip-migrate", action="store_true",
                        help="只验证入库与检索，不执行迁移")
    args = parser.parse_args()

    samples = Path(args.samples).resolve()
    if not samples.is_dir():
        print(f"样例目录不存在：{samples}")
        return 1

    health = wait_ready(args.base_url)
    print(f"服务就绪：version={health.get('version')} records={health.get('records')}")
    embed = api(args.base_url, "GET", "/api/v1/embed")
    print(f"当前模型：{embed['current']}（{embed['dimension']} 维）")

    print("\n[导入] 记忆与样例文件")
    n_mem = import_memories(args.base_url, samples, args.project)
    print(f"  记忆：{n_mem} 条")
    files = import_files(args.base_url, samples, args.project)
    for item in files:
        print(f"  {item['file']}: {item['chunks']} 块"
              f"（其中表格行 {item['table_chunks']}）")

    before_records = active_records(args.base_url)
    print(f"\n[校验] 迁移前（当前模型记录 {before_records}）")
    failed = print_checks(expected_checks(args.base_url, args.project))

    if args.skip_migrate:
        print(f"\n结果：{'PASS' if failed == 0 else 'FAIL'}（已跳过迁移）")
        return 0 if failed == 0 else 1

    if args.target == embed["current"]:
        print(f"\n目标模型与当前模型相同（{args.target}），无需迁移。")
        return 0 if failed == 0 else 1

    info = migrate(args.base_url, args.target, args.timeout, args.poll)
    if info.get("status") != "done":
        print(f"\n[迁移失败] {info.get('error')}")
        print("旧库与旧模型保持可用，可修复后重试。")
        return 1
    print(f"[迁移完成] {info.get('source')} → {info.get('target')}，"
          f"{info.get('done')}/{info.get('total')} 条")

    after_embed = api(args.base_url, "GET", "/api/v1/embed")
    print(f"迁移后模型：{after_embed['current']}（{after_embed['dimension']} 维）")
    after_records = active_records(args.base_url)
    print(f"\n[校验] 迁移后（当前模型记录 {after_records}）")
    failed += print_checks(expected_checks(args.base_url, args.project))

    def record_count():
        assert after_records == before_records, \
            f"记录数变化：{before_records} → {after_records}"

    def model_switched():
        assert after_embed["current"] == args.target, \
            f"模型未切换：{after_embed['current']}"
        models = {m["model"]: m for m in after_embed.get("models") or []}
        assert models.get(args.target, {}).get("active") is True, "目标模型未标记为当前"
        assert models.get(info.get("source"), {}).get("records", 0) > 0, \
            "旧模型数据未保留"

    failed += print_checks([check("记录数不变", record_count),
                            check("模型已切换且旧数据保留", model_switched)],
                           prefix="迁移后：")

    if args.delete_old and info.get("source"):
        old = info["source"]
        resp = api(args.base_url, "POST", "/api/v1/embed/models/delete",
                   {"model": old, "confirm": old})
        print(f"\n[清理] 已删除旧模型 {old} 的 {resp.get('deleted')} 条数据")

    print(f"\n结果：{'PASS' if failed == 0 else 'FAIL'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
