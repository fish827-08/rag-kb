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
_SHEET_RE = re.compile(r"^\[([^\]]{1,60})\]\s")
# 非指标列：求和/平均时排除（学号、序号等标识列，以及已有的汇总列）
_NON_VALUE_KEYS = {"__sheet", "学号", "编号", "序号", "工号", "id", "ID",
                   "总分", "合计", "平均", "均值"}
_SHEET_KEY = "__sheet"


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
    """读取 XLSX/XLSM：openpyxl 只读模式逐 sheet 取行；缺失依赖给安装指引。

    v2.4.1：同时读「缓存值」与「公式」两遍。只有公式、没有缓存值的单元格
    （如脚本生成文件的总分列）保留公式文本，避免整列被当成空值丢弃。
    English: Read both cached values and formulas. A formula-only cell (e.g. a
    generated workbook whose total column has no cached value) keeps the formula
    text instead of being dropped as empty."""
    openpyxl = _ingest_import("openpyxl", "解析 Excel ")
    try:
        valued = openpyxl.load_workbook(str(path), read_only=True,
                                        data_only=True)
        formula = openpyxl.load_workbook(str(path), read_only=True,
                                         data_only=False)
    except Exception as exc:
        raise UnsupportedFormatError(
            f"Excel 解析失败（{path.name}）：{exc}") from exc
    tables = []
    try:
        formula_sheets = {s.title: s for s in formula.worksheets}
        for sheet in valued.worksheets:
            sheet_f = formula_sheets.get(sheet.title)
            raw = []
            row_fs = sheet_f.iter_rows(values_only=True) if sheet_f else None
            for row in sheet.iter_rows(values_only=True):
                row_f = next(row_fs, ()) if row_fs is not None else ()
                cells = []
                for idx, value in enumerate(row):
                    f = row_f[idx] if idx < len(row_f) else None
                    if value is None and isinstance(f, str) and f.startswith("="):
                        cells.append(f)  # 仅公式、无缓存值：保留公式文本
                    else:
                        cells.append("" if value is None else _cell_text(value))
                raw.append(cells)
            tables.append({"sheet": sheet.title or "Sheet", "raw": raw})
    finally:
        for wb in (valued, formula):
            try:
                wb.close()
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
        v = (v.replace("\r", " ").replace("\n", " ")
             .replace(_CELL_SEP.strip(), "／").replace(_KV_SEP, "＝"))
        parts.append(f"{header}{_KV_SEP}{v}")
    return _CELL_SEP.join(parts)


def parse_row_text(text: str) -> dict | None:
    """解析一行 `列名=值 | ...`；无有效键值返回 None。

    多 sheet 表格的行首带 `[表名] ` 前缀：解析时保留到 `__sheet` 字段，
    供问答按班级/表名消歧，同时不污染列名。
    English: Parse one `column=value | ...` line. Multi-sheet tables prefix the line
    with `[sheet] `; the sheet name is kept in `__sheet` so Q&A can disambiguate
    without polluting column names."""
    raw = (text or "").strip()
    match = _SHEET_RE.match(raw)
    sheet = match.group(1).strip() if match else ""
    line = _SHEET_RE.sub("", raw)
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
    if not row:
        return None
    if sheet:
        row[_SHEET_KEY] = sheet
    return row


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


def _is_selector(value: str) -> bool:
    """单元格值是否可作为「定位键」：非数字值直接可用；纯数字需 ≥4 位。

    防止分数 `50` 这类短数字误命中问题里的 `1500`（子串匹配陷阱）；
    学号/编号通常 4 位以上，仍可用于定位。
    English: Whether a cell value can act as a row selector. Non-numeric values
    qualify; numeric values need at least 4 digits so short scores like `50` do
    not falsely match `1500` inside a question. IDs of 4+ digits still work."""
    v = (value or "").strip()
    if len(v) < 2:
        return False
    if _as_number(v) is None:
        return True
    return len(v) >= 4


# 表格意图词：问题里没有列名时，靠这些词判断「确实在问表格数据」，
# 避免「北京有哪些旅游景点」这类问题被城市表劫持。
_INTENT_WORDS = ("成绩", "分数", "得分", "总分", "合计", "平均", "电话", "手机",
                 "地址", "邮箱", "年龄", "薪资", "工资", "价格", "数量", "排名",
                 "score", "phone", "address", "salary", "price", "average",
                 "total")
_META_KEYS = {_SHEET_KEY, "_record_id"}


def _row_content_key(row: dict) -> tuple:
    """去重键：忽略 sheet/记录 ID 等元字段，只比较真实单元格。"""
    return tuple(sorted((k, v) for k, v in row.items() if k not in _META_KEYS))


def match_table_rows(rows: list[dict], question: str):
    """匹配表格行，返回 (候选行, 列名命中, 命中值) 或 None。

    规则（v2.4.1 收紧）：
    1) 问题必须命中列名，或包含表格意图词（成绩/总分/电话等），否则不判为表格问题；
    2) 先按表名过滤（最长匹配）；单元格值用 `_is_selector` 防短数字误命中；
    3) 同名行去重；多个前缀候选（用户5/55/550）只保留最长命中值对应的行。

    English: Match table rows, returning (rows, header hits, matched values) or None.
    The question must hit a column name or contain a table-intent word; sheet names
    filter first (longest match); short numbers are not selectors; duplicate rows are
    collapsed and longer prefix matches win.
    """
    q = (question or "").strip().lower()
    if not q or not rows:
        return None
    sheet_names = {r.get(_SHEET_KEY) for r in rows if r.get(_SHEET_KEY)}
    matched_sheets = sorted((s for s in sheet_names if s and s.lower() in q),
                            key=len, reverse=True)
    if matched_sheets:
        rows = [r for r in rows if r.get(_SHEET_KEY) == matched_sheets[0]]
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in _META_KEYS and key not in headers:
                headers.append(key)
    header_hits = [h for h in headers if h.lower() in q]
    if not header_hits and not any(w in q for w in _INTENT_WORDS):
        return None
    candidates: list[tuple[dict, list[str]]] = []
    for row in rows:
        hits = [v for k, v in row.items()
                if k not in _META_KEYS and _is_selector(v) and v.lower() in q]
        if hits:
            candidates.append((row, hits))
    if not candidates:
        return None
    # 同内容行去重（项目库 + 全局库同时存在同一张表时只答一次）
    seen: set[tuple] = set()
    unique: list[tuple[dict, list[str]]] = []
    for row, hits in candidates:
        key = _row_content_key(row)
        if key in seen:
            continue
        seen.add(key)
        unique.append((row, hits))
    # 前缀消歧：只保留最长命中值的行（用户550 不再同时命中 用户5/55）
    best_len = max(max(len(v) for v in hits) for _, hits in unique)
    unique = [(row, hits) for row, hits in unique
              if max(len(v) for v in hits) == best_len]
    matched_values = [v for _, hits in unique for v in hits]
    return [row for row, _ in unique], header_hits, matched_values


def query_table(rows: list[dict], question: str):
    """确定性表格查询，返回 (answer_text, matched_rows) 或 None。

    English: Deterministic table query returning (answer_text, matched_rows) or None.
    """
    info = match_table_rows(rows, question)
    if not info:
        return None
    matched, header_hits, matched_values = info
    q = (question or "").strip().lower()
    want_sum = any(w in q for w in _SUM_WORDS)
    want_avg = any(w in q for w in _AVG_WORDS)

    def _label(row: dict) -> str:
        """行标识：优先非数字命中值（姓名/编号），其次数字；多 sheet 时前置表名。"""
        base = ""
        for v in row.values():
            if v in matched_values and _as_number(v) is None:
                base = v
                break
        if not base:
            for v in row.values():
                if v in matched_values:
                    base = v
                    break
        if not base:
            for k, v in row.items():
                if k not in _META_KEYS and _as_number(v) is None:
                    base = v
                    break
        if not base:
            base = next((v for k, v in row.items() if k not in _META_KEYS), "")
        sheet = row.get(_SHEET_KEY)
        return f"{sheet} {base}".strip() if sheet else base

    def _value_cols(row: dict, explicit: list[str]) -> list[str]:
        """参与聚合的数值列：显式列优先，否则排除学号/总分等非指标列。"""
        cols = [c for c in explicit
                if c not in _META_KEYS and _as_number(row.get(c)) is not None]
        if cols:
            return cols
        return [c for c in row
                if c not in _META_KEYS and c not in _NON_VALUE_KEYS
                and _as_number(row.get(c)) is not None]

    lines = []
    for row in matched:
        label = _label(row)
        explicit = [h for h in header_hits if h in row]
        if want_sum or want_avg:
            # 问题明确问「总分」且表里已有总分类：直接读该列，不重复求和
            if want_sum and "总分" in explicit and row.get("总分"):
                lines.append(f"{label}：总分={row['总分']}")
                continue
            cols = _value_cols(row, explicit)
            nums = [_as_number(row[c]) for c in cols]
            nums = [n for n in nums if n is not None]
            if nums:
                total = sum(nums)
                if want_avg:
                    lines.append(f"{label}：平均={_fmt_number(total / len(nums))}")
                else:
                    lines.append(f"{label}：总分={_fmt_number(total)}")
                continue
        if explicit:
            # 已经作为行标识的列（如「用户=用户550」）不重复输出
            cols = [c for c in explicit
                    if row.get(c) not in matched_values] or explicit
        else:
            cols = [k for k in row if k not in _META_KEYS]
        parts = [f"{c}={row[c]}" for c in cols if row.get(c)]
        if parts:
            lines.append(f"{label}：" + "，".join(parts))
    if not lines:
        return None
    return "\n".join(lines), matched


def query_rows(rows: list[dict], question: str) -> str | None:
    """兼容入口：只返回答案文本（测试与旧调用方使用）。"""
    result = query_table(rows, question)
    return result[0] if result else None
