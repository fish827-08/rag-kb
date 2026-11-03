"""表格文件解析与逐行问答（v2.4，设计见 docs/superpowers/specs/2026-11-03-kb-v2.4-design.md）。

支持 CSV 与 XLSX/XLSM：CSV 用标准库 csv（utf-8-sig → gbk 回退），
XLSX 用可选依赖 openpyxl 逐 sheet 读取。每行统一转成
`列名=值 | 列名=值` 文本，供向量/BM25 检索，也供确定性问答逐行解析。

English: Table parsing and row-level Q&A (v2.4). CSV uses the stdlib csv module
(utf-8-sig then gbk fallback); XLSX/XLSM uses the optional openpyxl dependency.
Each row is rendered as `column=value | column=value` text for retrieval and
deterministic row-level answering.
"""
import csv
import io
import re
from pathlib import Path

from kb.ingest import UnsupportedFormatError, _ingest_import

TABLE_EXTS = {".csv", ".xlsx", ".xlsm"}
_CELL_SEP = " | "
_KV_SEP = "="
# 聚合关键词：总分/合计/求和 与 平均/均值
_SUM_WORDS = ("总分", "合计", "求和", "总和", "sum")
_AVG_WORDS = ("平均", "均值", "average", "avg")
_SHEET_RE = re.compile(r"^\[[^\]]{1,60}\]\s")


def parse_table_file(path: Path | str) -> list[dict]:
    """解析表格文件为 [{sheet, headers, rows}]；不支持/依赖缺失抛 UnsupportedFormatError。"""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".csv":
        return _parse_csv(path)
    if ext in (".xlsx", ".xlsm"):
        return _parse_xlsx(path)
    if ext == ".xls":
        raise UnsupportedFormatError(
            "旧版 .xls 暂不支持，请用 Excel 另存为 .xlsx 后重试")
    raise UnsupportedFormatError(f"不是表格文件：{ext or '<无扩展名>'}（{path.name}）")


def _parse_csv(path: Path) -> list[dict]:
    """读取 CSV：首行表头，跳过全空行；编码尝试 utf-8-sig → gbk → utf-8(容错)。"""
    text = None
    for enc in ("utf-8-sig", "gbk"):
        try:
            text = path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = path.read_text(encoding="utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    raw = list(reader)
    return _normalize_tables([{"sheet": "CSV", "raw": raw}], path.name)


def _parse_xlsx(path: Path) -> list[dict]:
    """读取 XLSX/XLSM：openpyxl 只读模式逐 sheet 取行；缺失依赖给安装指引。"""
    openpyxl = _ingest_import("openpyxl", "解析 Excel ")
    try:
        workbook = openpyxl.load_workbook(str(path), read_only=True,
                                          data_only=True)
    except Exception as exc:
        raise UnsupportedFormatError(
            f"Excel 解析失败（{path.name}）：{exc}") from exc
    tables = []
    try:
        for sheet in workbook.worksheets:
            raw = []
            for row in sheet.iter_rows(values_only=True):
                raw.append(["" if v is None else _cell_text(v) for v in row])
            tables.append({"sheet": sheet.title or "Sheet", "raw": raw})
    finally:
        try:
            workbook.close()
        except Exception:
            pass
    return _normalize_tables(tables, path.name)


def _cell_text(value) -> str:
    """单元格值转文本：整数型浮点去掉 .0，其余 str 后去首尾空白。"""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _normalize_tables(tables: list[dict], filename: str) -> list[dict]:
    """把原始行归一为 {sheet, headers, rows}：首非空行作表头，补空列名。"""
    result = []
    for table in tables:
        raw = table["raw"]
        cleaned = [[("" if c is None else str(c).strip()) for c in row]
                   for row in raw]
        cleaned = [row for row in cleaned if any(cell for cell in row)]
        if not cleaned:
            continue
        headers = [cell or f"列{i + 1}" for i, cell in enumerate(cleaned[0])]
        rows = []
        for row in cleaned[1:]:
            if len(row) < len(headers):
                row = row + [""] * (len(headers) - len(row))
            rows.append(row[:len(headers)])
        if rows:
            result.append({"sheet": table["sheet"], "headers": headers,
                           "rows": rows})
    if not result:
        raise UnsupportedFormatError(f"表格为空或没有数据行：{filename}")
    return result


def row_to_text(headers: list[str], row: list[str]) -> str:
    """一行转 `列名=值 | 列名=值`（跳过空值；值内的分隔符做转义替换）。"""
    parts = []
    for header, value in zip(headers, row):
        v = (value or "").strip()
        if not v:
            continue
        v = v.replace(_CELL_SEP.strip(), "／").replace(_KV_SEP, "＝")
        parts.append(f"{header}{_KV_SEP}{v}")
    return _CELL_SEP.join(parts)


def parse_row_text(text: str) -> dict | None:
    """解析一行 `列名=值 | ...`；无有效键值返回 None（兼容 [sheet] 前缀）。"""
    line = _SHEET_RE.sub("", (text or "").strip())
    if not line:
        return None
    row: dict[str, str] = {}
    for part in line.split(_CELL_SEP):
        if _KV_SEP not in part:
            continue
        key, value = part.split(_KV_SEP, 1)
        key, value = key.strip(), value.strip()
        if key and value:
            row[key] = value
    return row or None


def rows_from_content(content: str) -> list[dict]:
    """把一条表格记录的 content（可能含多行）拆成若干 {列名: 值}。"""
    rows = []
    for line in (content or "").splitlines():
        row = parse_row_text(line)
        if row:
            rows.append(row)
    return rows


def _as_number(value: str) -> float | None:
    """值转数字；带千分位/百分号也尝试，失败返回 None。"""
    v = (value or "").strip().replace(",", "").replace("，", "")
    if v.endswith("%"):
        try:
            return float(v[:-1])
        except ValueError:
            return None
    try:
        return float(v)
    except ValueError:
        return None


def _fmt_number(value: float) -> str:
    """数字输出：整数去掉小数位，否则保留两位。"""
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def query_rows(rows: list[dict], question: str) -> str | None:
    """确定性表格查询：按单元格值定位行、按列名筛选列，支持求和/平均。

    命中规则：
    - 问题中出现某行任一单元格值（长度 ≥2）→ 该行为候选；
    - 问题中出现列名 → 该列作为输出过滤（可多列）；
    - 含「总分/合计/求和」或「平均/均值」时对数值列聚合。
    无候选行返回 None（调用方回落到 RAG，不改变既有行为）。

    English: Deterministic table query: locate rows by cell values and filter
    columns by header names, with sum/average aggregation. Returns None when no
    row matches so the caller falls back to the regular RAG path.
    """
    q = (question or "").strip().lower()
    if not q or not rows:
        return None
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    header_hits = [h for h in headers if h.lower() in q and len(h) >= 1]
    matched = []
    matched_values = []
    for row in rows:
        hits = [v for v in row.values() if len(v) >= 2 and v.lower() in q]
        if hits:
            matched.append(row)
            matched_values.extend(hits)
    if not matched:
        return None
    want_sum = any(w in q for w in _SUM_WORDS)
    want_avg = any(w in q for w in _AVG_WORDS)

    def _label(row: dict) -> str:
        """行标识：优先命中值，其次首个非数字值，最后首列值。"""
        for v in row.values():
            if v in matched_values:
                return v
        for k, v in row.items():
            if _as_number(v) is None:
                return v
        return next(iter(row.values()))

    lines = []
    for row in matched:
        label = _label(row)
        numeric_cols = [k for k, v in row.items() if _as_number(v) is not None]
        if want_sum or want_avg:
            cols = [h for h in header_hits if h in numeric_cols] or numeric_cols
            nums = [_as_number(row[c]) for c in cols]
            nums = [n for n in nums if n is not None]
            if nums:
                total = sum(nums)
                if want_avg:
                    lines.append(f"{label}：平均={_fmt_number(total / len(nums))}")
                else:
                    lines.append(f"{label}：总分={_fmt_number(total)}")
                continue
        cols = [h for h in header_hits if h in row] or list(row.keys())
        parts = [f"{c}={row[c]}" for c in cols if row.get(c)]
        if parts:
            lines.append(f"{label}：" + "，".join(parts))
    return "\n".join(lines) if lines else None
