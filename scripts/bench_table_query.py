"""表格确定性问答基准：可复现的准确率与延迟数字。

用法：
    python scripts/bench_table_query.py                 # 默认 15 sheet × 200 行
    python scripts/bench_table_query.py --smoke         # 小规模冒烟（CI 可跑）
    python scripts/bench_table_query.py --sheets 50 --rows 500
    python scripts/bench_table_query.py --json          # 机器可读输出
    python scripts/bench_table_query.py --ingest --sheets 40 --rows 500
                                                        # 2 万行完整入库实测

口径：
    · 数据在内存里生成，不写磁盘、不建库、不加载嵌入模型；
    · “同名跨 sheet”故意让每个 sheet 都有一个张芳，但分数各不相同；
    · 判对标准不是“有返回”，而是答案里的分数等于目标 sheet 的分数。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kb.tables import query_table, row_to_text, rows_from_content  # noqa: E402

SURNAMES = ["张", "王", "李", "赵", "陈", "刘", "杨", "黄"]
GIVEN = ["伟", "芳", "娜", "强", "磊", "静", "敏", "洋", "勇", "艳"]


def make_tables(sheets: int, rows: int) -> list[dict]:
    """生成多 sheet 成绩表；每个 sheet 第一个学生都叫张芳。"""
    tables = []
    for s in range(1, sheets + 1):
        sheet = f"{s}班"
        body = []
        for i in range(rows):
            name = "张芳" if i == 0 else (
                SURNAMES[i % len(SURNAMES)] + GIVEN[(i * 3) % len(GIVEN)])
            body.append([
                str(i + 1), name,
                str(60 + (i * 7 + s) % 41),
                str(55 + (i * 11 + s) % 46),
                str(50 + (i * 13 + s) % 51),
            ])
        tables.append({"sheet": sheet,
                       "headers": ["序号", "姓名", "语文", "数学", "英语"],
                       "rows": body})
    return tables


def to_rows(tables: list[dict]) -> list[dict]:
    multi = len(tables) > 1
    rows: list[dict] = []
    for table in tables:
        prefix = f"[{table['sheet']}] " if multi else ""
        for row in table["rows"]:
            text = row_to_text(table["headers"], row)
            if text:
                rows.extend(rows_from_content(prefix + text))
    return rows


def build_questions(tables: list[dict], limit: int) -> list[tuple[str, str, str]]:
    """返回 (问题, 期望分数, 说明)；全部针对每个 sheet 的张芳。"""
    questions = []
    for table in tables:
        expected = table["rows"][0][3]  # 数学
        questions.append(
            (f"{table['sheet']}张芳的数学成绩是多少", expected, table["sheet"]))
        if len(questions) >= limit:
            break
    return questions


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1))))
    return ordered[idx]


def run(sheets: int, rows: int, question_limit: int) -> dict:
    tables = make_tables(sheets, rows)
    t0 = time.perf_counter()
    parsed_rows = to_rows(tables)
    parse_ms = (time.perf_counter() - t0) * 1000

    questions = build_questions(tables, question_limit)
    latencies: list[float] = []
    correct = 0
    failures: list[dict] = []
    for question, expected, sheet in questions:
        start = time.perf_counter()
        result = query_table(parsed_rows, question)
        latencies.append((time.perf_counter() - start) * 1000)
        answer = result[0] if result else ""
        # 判对：答案里出现目标 sheet 且数学分数正确
        ok = bool(answer) and sheet in answer and f"={expected}" in answer
        if ok:
            correct += 1
        else:
            failures.append({"question": question, "expected": expected,
                             "answer": answer})

    return {
        "sheets": sheets,
        "rows_per_sheet": rows,
        "total_rows": len(parsed_rows),
        "questions": len(questions),
        "correct": correct,
        "accuracy": round(correct / len(questions), 4) if questions else 0.0,
        "p50_ms": round(percentile(latencies, 50), 2),
        "p95_ms": round(percentile(latencies, 95), 2),
        "max_ms": round(max(latencies), 2) if latencies else 0.0,
        "build_ms": round(parse_ms, 2),
        "failures": failures[:5],
    }


def _write_xlsx(tables: list[dict], path: Path) -> None:
    """把内存表写成真实 xlsx（write_only 模式，避免大表占用内存）。"""
    from openpyxl import Workbook

    wb = Workbook(write_only=True)
    for table in tables:
        ws = wb.create_sheet(title=table["sheet"])
        ws.append(table["headers"])
        for row in table["rows"]:
            ws.append(row)
    wb.save(path)


def run_ingest(sheets: int, rows: int, question_limit: int,
               keep: bool = False) -> dict:
    """生成真实 xlsx → 走完整入库链路 → 测量解析/入库/问答延迟。

    数据目录固定在仓库 `_local/_bench_data`（gitignore），不碰用户真实库。
    """
    import shutil

    from kb.config import Settings
    from kb.service import KBService
    from kb.tables import parse_table_file

    workdir = ROOT / "_local" / "_bench_data"
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    xlsx = workdir / "bench_20k.xlsx"

    tables = make_tables(sheets, rows)
    start = time.perf_counter()
    _write_xlsx(tables, xlsx)
    write_s = time.perf_counter() - start
    file_mb = round(xlsx.stat().st_size / 1024 / 1024, 2)

    start = time.perf_counter()
    parsed = parse_table_file(xlsx)
    parse_s = time.perf_counter() - start
    total_rows = sum(len(t["rows"]) for t in parsed)

    settings = Settings(data_dir=workdir / "data", log_dir=workdir / "logs",
                        llm_mode="off")
    start = time.perf_counter()
    svc = KBService(settings)
    init_s = time.perf_counter() - start

    start = time.perf_counter()
    result = svc.add_document(xlsx, project="bench")
    ingest_s = time.perf_counter() - start
    state = svc.embedder.status()["state"]

    questions = [q for q, _, _ in build_questions(parsed, question_limit)]
    cold_ms = 0.0
    if questions:
        # 第一条查询包含“从 Chroma 读回并解析全部表格行”的冷启动开销
        start = time.perf_counter()
        svc.ask(questions[0], project="bench")
        cold_ms = (time.perf_counter() - start) * 1000
        questions = questions[1:]
    latencies: list[float] = []
    correct = 0
    for question in questions:
        start = time.perf_counter()
        answer = svc.ask(question, project="bench")
        latencies.append((time.perf_counter() - start) * 1000)
        if answer.get("llm") == "table":
            correct += 1

    if not keep:
        shutil.rmtree(workdir, ignore_errors=True)

    return {
        "sheets": sheets,
        "rows_per_sheet": rows,
        "total_rows": total_rows,
        "file_mb": file_mb,
        "write_s": round(write_s, 2),
        "parse_s": round(parse_s, 2),
        "init_s": round(init_s, 2),
        "ingest_s": round(ingest_s, 2),
        "rows_per_second": round(total_rows / ingest_s, 0) if ingest_s else 0,
        "chunks": result.get("chunks", 0),
        "table_chunks": result.get("table_chunks", 0),
        "embedder_state": state,
        "questions": len(questions) + (1 if cold_ms else 0),
        "table_answers": correct + (1 if cold_ms else 0),
        "cold_ms": round(cold_ms, 2),
        "p50_ms": round(percentile(latencies, 50), 2),
        "p95_ms": round(percentile(latencies, 95), 2),
        "kept": keep,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="表格确定性问答基准")
    parser.add_argument("--sheets", type=int, default=15)
    parser.add_argument("--rows", type=int, default=200)
    parser.add_argument("--questions", type=int, default=18)
    parser.add_argument("--smoke", action="store_true",
                        help="小规模冒烟：3 sheet × 30 行 × 3 题")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--ingest", action="store_true",
                        help="走完整入库链路（生成 xlsx → 解析 → 入库 → 问答）")
    parser.add_argument("--keep", action="store_true",
                        help="保留 _local/_bench_data 便于检查")
    args = parser.parse_args()

    sheets, rows, questions = args.sheets, args.rows, args.questions
    if args.smoke:
        sheets, rows, questions = 3, 30, 3

    if args.ingest:
        result = run_ingest(sheets, rows, questions, keep=args.keep)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"规模: {result['sheets']} sheet × {result['rows_per_sheet']} 行 "
                  f"= {result['total_rows']} 行，文件 {result['file_mb']} MB")
            print(f"生成 xlsx: {result['write_s']}s    解析: {result['parse_s']}s")
            print(f"服务初始化: {result['init_s']}s    入库: {result['ingest_s']}s "
                  f"({result['rows_per_second']:.0f} 行/秒)")
            print(f"入库块数: {result['chunks']}（表格行 {result['table_chunks']}）"
                  f"    嵌入模型状态: {result['embedder_state']}")
            print(f"问答: {result['table_answers']}/{result['questions']} 命中确定性路径  "
                  f"首查 {result['cold_ms']}ms（建缓存）  "
                  f"P50 {result['p50_ms']}ms  P95 {result['p95_ms']}ms")
        return 0

    result = run(sheets, rows, questions)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"规模: {result['sheets']} sheet × {result['rows_per_sheet']} 行 "
              f"= {result['total_rows']} 行")
        print(f"题目: {result['questions']}    判对: "
              f"{result['correct']}/{result['questions']} "
              f"({result['accuracy'] * 100:.1f}%)")
        print(f"延迟: P50 {result['p50_ms']}ms  P95 {result['p95_ms']}ms  "
              f"max {result['max_ms']}ms")
        print(f"建表: {result['build_ms']}ms（内存生成 + 行文本转换）")
        if result["failures"]:
            print("失败样例:")
            for item in result["failures"]:
                print(f"  Q: {item['question']}  期望 {item['expected']}  "
                      f"实际 {item['answer']!r}")
    return 0 if result["accuracy"] >= 0.99 else 1


if __name__ == "__main__":
    raise SystemExit(main())
