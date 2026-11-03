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
from datetime import date, datetime
from pathlib import Path

from kb.ingest import UnsupportedFormatError, _ingest_import

TABLE_EXTS = {".csv", ".xlsx", ".xlsm", ".xls"}
_CELL_SEP = " | "
_KV_SEP = "="
# 聚合关键词：总分/合计/求和 与 平均/均值
_SUM_WORDS = ("总分", "合计", "求和", "总和", "sum")
_AVG_WORDS = ("平均", "均值", "average", "avg")
_LIST_WORDS = ("有哪些", "哪些", "列出", "列一下", "列个", "名单", "列表", "所有")
_SHEET_RE = re.compile(r"^\[([^\]]{1,60})\]\s")
# 非指标列：求和/平均时排除（学号、序号等标识列，以及已有的汇总列）
_NON_VALUE_KEYS = {"__sheet", "学号", "编号", "序号", "工号", "id", "ID",
                   "总分", "合计", "平均", "均值"}
_SHEET_KEY = "__sheet"

# 表头识别：真实业务表常在表头上方放一行标题（甚至“附件”），
# 不能把第一行直接当表头。扫描前 10 行，取非空单元格最多、且含表头关键词的一行。
_HEADER_SCAN_ROWS = 10
_HEADER_HINTS = ("序号", "编号", "名称", "单位", "岗位", "职位", "电话", "联系人",
                 "地点", "地址", "学历", "专业", "年龄", "薪酬", "薪资", "工资",
                 "待遇", "要求", "名额", "人数", "数量", "备注", "分数", "成绩",
                 "班级", "姓名")

# 列名同义词：概念 → 常见写法（长词在前，避免“电话”先于“联系电话”命中）。
# 匹配只看表头去掉括号单位后的主干，因此“薪酬待遇（元/月）”与“薪资福利待遇（…）”
# 都会归到「薪酬待遇」。
_COLUMN_SYNONYMS: dict[str, tuple[str, ...]] = {
    "序号": ("序号", "编号"),
    "企业名称": ("企业名称", "公司名称", "单位名称", "招聘单位", "用人单位",
                 "用工单位", "企业"),
    "主管部门": ("主管部门", "部门"),
    "招聘岗位": ("招聘岗位", "岗位名称", "职位名称", "岗位", "职位"),
    "招聘名额": ("招聘名额", "招聘人数", "名额", "人数", "数量"),
    "学历要求": ("学历要求", "最低学历", "学历"),
    "专业要求": ("专业要求", "专业"),
    "年龄要求": ("年龄要求", "年龄"),
    "其他要求": ("其他要求", "任职要求", "岗位要求"),
    "薪酬待遇": ("薪酬福利待遇", "薪资福利待遇", "薪酬待遇", "薪资待遇",
                 "薪酬", "薪资", "工资", "待遇", "月薪", "年薪"),
    "工作地点": ("工作地点", "上班地点", "工作地址", "上班地址", "地点", "地址"),
    "联系人": ("联系人", "联络人", "联系人姓名"),
    "联系电话": ("联系电话", "咨询电话", "联系方式", "电话号码", "手机号码",
                 "电话", "手机"),
    "备注": ("备注", "说明"),
}
# 合并单元格补全：这些列在“一个公司多个岗位”的续行里通常为空，
# 空值沿用上一行；岗位/薪酬/名额等列不补，避免把不同岗位写成同一个。
_FILL_CONCEPTS = {"企业名称", "主管部门", "联系人", "联系电话", "工作地点"}


def parse_table_file(path: Path | str) -> list[dict]:
    """解析表格文件为 [{sheet, headers, rows}]；不支持/依赖缺失抛 UnsupportedFormatError。"""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".csv":
        return _parse_csv(path)
    if ext in (".xlsx", ".xlsm"):
        return _parse_xlsx(path)
    if ext == ".xls":
        return _parse_xls(path)
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
            # 部分导出工具（财务系统、BI、WPS 另存）不写 <dimension>，
            # read_only 模式会因此读到 0 行，必须重置维度后重新扫描。
            # English: Some exporters omit <dimension>; read_only mode then sees
            # zero rows, so reset the dimensions before scanning.
            if hasattr(sheet, "reset_dimensions"):
                sheet.reset_dimensions()
            if sheet_f is not None and hasattr(sheet_f, "reset_dimensions"):
                sheet_f.reset_dimensions()
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


def _parse_xls(path: Path) -> list[dict]:
    """读取旧版 .xls（BIFF）：xlrd 只读逐 sheet 取行；缺失依赖给安装指引。

    .xls 在招聘/政府类表格里仍很常见，不能只提示用户另存为 .xlsx。
    English: Read legacy .xls (BIFF) with xlrd sheet by sheet; missing dependency
    yields install guidance. Legacy .xls is still common in HR/government sheets,
    so asking users to re-save as .xlsx is not acceptable."""
    xlrd = _ingest_import("xlrd", "解析旧版 Excel ")
    try:
        book = xlrd.open_workbook(str(path), on_demand=True)
    except Exception as exc:
        raise UnsupportedFormatError(
            f"Excel 解析失败（{path.name}）：{exc}") from exc
    tables = []
    try:
        for sheet in book.sheets():
            if sheet.nrows == 0 or sheet.ncols == 0:
                continue
            raw = []
            for r in range(sheet.nrows):
                cells = []
                for c in range(sheet.ncols):
                    cell = sheet.cell(r, c)
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            dt = xlrd.xldate_as_datetime(cell.value,
                                                         book.datemode)
                            cells.append(dt.strftime("%Y-%m-%d"))
                            continue
                        except Exception:
                            pass
                    cells.append(_cell_text(cell.value))
                raw.append(cells)
            tables.append({"sheet": sheet.name or "Sheet", "raw": raw})
    finally:
        try:
            book.release_resources()
        except Exception:
            pass
    return _normalize_tables(tables, path.name)


def _cell_text(value) -> str:
    """单元格值转文本：日期去时分秒，整数型浮点去掉 .0。"""
    if isinstance(value, datetime):
        if value.time() == datetime.min.time():
            return value.strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _header_base(header: str) -> str:
    """表头主干：去掉括号里的单位/说明、空白并转小写。"""
    base = re.sub(r"[（(【\[].*?[）)】\]]", "", header or "")
    return re.sub(r"\s+", "", base).lower()


def _header_concept(header: str) -> str:
    """把表头归到同义词概念；未知表头返回空串。"""
    base = _header_base(header)
    if not base:
        return ""
    for concept, aliases in _COLUMN_SYNONYMS.items():
        for alias in aliases:
            if alias.lower() in base:
                return concept
    return ""


def _pick_header_row(cleaned: list[list[str]]) -> int:
    """在前 10 行里挑表头：非空单元格多、且含表头关键词的行优先。"""
    best, best_score = 0, -1
    for i in range(min(len(cleaned), _HEADER_SCAN_ROWS)):
        nonempty = sum(1 for cell in cleaned[i] if cell)
        if nonempty < 2:
            continue
        text = "".join(cleaned[i])
        hints = sum(1 for word in _HEADER_HINTS if word in text)
        score = nonempty * 2 + hints * 3
        if score > best_score:
            best, best_score = i, score
    return best


def _make_headers(row: list[str]) -> list[str]:
    """生成唯一表头：空列补“列N”，重名追加 _2/_3。"""
    headers: list[str] = []
    used: dict[str, int] = {}
    for i, cell in enumerate(row):
        name = re.sub(r"\s+", "", cell or "") or f"列{i + 1}"
        if name in used:
            used[name] += 1
            name = f"{name}_{used[name]}"
        else:
            used[name] = 1
        headers.append(name)
    return headers


def _is_footer(row: list[str]) -> bool:
    """单格“备注/说明”行不是数据行，跳过。"""
    values = [cell for cell in row if cell]
    return len(values) == 1 and values[0].startswith(("备注", "说明", "注：", "注:"))


def _fill_merged_cells(headers: list[str], rows: list[list[str]]) -> None:
    """补齐纵向合并单元格：公司/联系人/地点等空值沿用上一行。"""
    concepts = [_header_concept(h) for h in headers]
    prev: list[str] | None = None
    for row in rows:
        if prev:
            for i, concept in enumerate(concepts):
                if concept in _FILL_CONCEPTS and not row[i] and prev[i]:
                    row[i] = prev[i]
        prev = row


def _normalize_tables(tables: list[dict], filename: str) -> list[dict]:
    """把原始行归一为 {sheet, headers, rows}。

    真实业务表常在表头上方放标题行（.xls 甚至有两行），因此先找表头行，
    再取其后数据；纵向合并单元格按关键列续行补全。
    English: Normalize raw rows into {sheet, headers, rows}. Real business
    sheets often have title rows above the header (legacy .xls even has two),
    so detect the header row first, then read data below it; merged cells are
    forward-filled for key columns."""
    result = []
    for table in tables:
        raw = table["raw"]
        cleaned = [[("" if c is None else str(c).strip()) for c in row]
                   for row in raw]
        cleaned = [row for row in cleaned if any(cell for cell in row)]
        if not cleaned:
            continue
        header_idx = _pick_header_row(cleaned)
        headers = _make_headers(cleaned[header_idx])
        rows = []
        for row in cleaned[header_idx + 1:]:
            if len(row) < len(headers):
                row = row + [""] * (len(headers) - len(row))
            row = row[:len(headers)]
            if _is_footer(row):
                continue
            rows.append(row)
        if rows:
            _fill_merged_cells(headers, rows)
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


def _is_formula(value: str) -> bool:
    """单元格是否只是公式文本（无缓存结果）。

    openpyxl data_only 读到 None 时，_parse_xlsx 会保留公式原文；这类值
    不能当成答案展示，否则用户看到的是 `＝SUM(...)`。
    English: Whether a cell is formula text without a cached result; such values
    must not be shown as answers."""
    v = (value or "").strip()
    return v.startswith("=") or v.startswith("＝")


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
                 "地址", "邮箱", "年龄", "薪资", "工资", "价格", "金额",
                 "销售额", "数量", "排名", "成本", "毛利", "利润", "库存",
                 "score", "phone", "address", "salary", "price", "amount",
                 "average", "total")
_META_KEYS = {_SHEET_KEY, "_record_id"}
# 行标识列：反查电话/薪酬/地点时，标签应回到这些列，而不是命中值本身。
# _NAME_KEYS 优先于编号类 _IDENTITY_KEYS（客户名称优先于客户编号）。
_IDENTITY_CONCEPTS = {"企业名称", "主管部门"}
_NAME_KEYS = {"企业名称", "公司名称", "单位名称", "客户名称", "供应商名称",
              "项目名称", "产品名称", "姓名", "名称", "部门", "月份",
              "指标名称", "城市", "工作城市"}
_IDENTITY_KEYS = _NAME_KEYS | {"用户", "学生", "班级", "公司", "单位",
                               "用户名", "仓库", "工号", "订单编号",
                               "采购单号", "报销单号", "项目编号", "客户编号",
                               "供应商编号", "产品编号"}


def _row_content_key(row: dict) -> tuple:
    """去重键：忽略 sheet/记录 ID 等元字段，只比较真实单元格。"""
    return tuple(sorted((k, v) for k, v in row.items() if k not in _META_KEYS))


def _norm_sheet(text: str) -> str:
    """表名归一：中文数字转阿拉伯数字，去掉“第/份/括号”。

    让「（八月份第2期）」能匹配表名「8月第2期」。
    English: Normalize sheet names (Chinese numerals → digits, strip 第/份/parens)
    so "（八月份第2期）" matches the sheet "8月第2期"."""
    s = (text or "").lower()
    for ch in ("第", "份", "（", "）", "(", ")", " "):
        s = s.replace(ch, "")
    s = s.replace("月份", "月")
    for cn, digit in (("十二", "12"), ("十一", "11"), ("十", "10"),
                      ("一", "1"), ("二", "2"), ("三", "3"), ("四", "4"),
                      ("五", "5"), ("六", "6"), ("七", "7"), ("八", "8"),
                      ("九", "9"), ("零", "0")):
        s = s.replace(cn, digit)
    return s


def _filter_by_sheet(rows: list[dict], q: str) -> list[dict]:
    """按表名过滤（最长匹配），未指定表名时保留全部。"""
    names = {r.get(_SHEET_KEY) for r in rows if r.get(_SHEET_KEY)}
    if not names:
        return rows
    qn = _norm_sheet(q)
    hits = sorted((s for s in names if s and _norm_sheet(s) in qn),
                  key=lambda s: len(_norm_sheet(s)), reverse=True)
    return [r for r in rows if r.get(_SHEET_KEY) == hits[0]] if hits else rows


def _rows_headers(rows: list[dict]) -> list[str]:
    """从行里恢复列顺序。"""
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in _META_KEYS and key not in headers:
                headers.append(key)
    return headers


def _question_concepts(q: str) -> set[str]:
    """问题里出现了哪些列概念（含“薪资→薪酬待遇”“电话→联系电话”）。"""
    return {concept for concept, aliases in _COLUMN_SYNONYMS.items()
            if any(alias.lower() in q for alias in aliases)}


def _list_column(headers: list[str], q: str) -> str:
    """列表型问题（“有哪些企业/岗位”）要列出的列。

    取离“有哪些/哪些”最近的目标词：表名里常带“岗位清单”，不能因此把
    “有哪些企业”误判成列岗位。
    English: Pick the list target closest to the list phrase, so a sheet title
    containing “岗位清单” does not hijack “有哪些企业”."""
    list_pos = max((q.rfind(word) for word in _LIST_WORDS if word in q), default=-1)
    if list_pos < 0:
        return ""
    best, best_key = "", (10 ** 9, 1)
    for word, concept in (("岗位", "招聘岗位"), ("职位", "招聘岗位"),
                          ("电话", "联系电话"), ("地址", "工作地点"),
                          ("地点", "工作地点"), ("企业", "企业名称"),
                          ("公司", "企业名称"), ("单位", "企业名称")):
        pos = q.rfind(word)
        if pos < 0:
            continue
        hit = next((h for h in headers if _header_concept(h) == concept), "")
        if not hit:
            continue
        key = (abs(pos - list_pos), 0 if pos >= list_pos else 1)
        if key < best_key:
            best, best_key = hit, key
    if best:
        return best
    for concept in _COLUMN_SYNONYMS:
        if concept in _question_concepts(q):
            hit = next((h for h in headers if _header_concept(h) == concept), "")
            if hit:
                return hit
    return ""


def _salary_bounds(value: str):
    """薪酬区间：抽数字（支持“万”），返回 (最小, 最大) 或 None。"""
    nums = [float(n) * (10000 if unit else 1)
            for n, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(万)?", value or "")]
    if not nums:
        return None
    return min(nums), max(nums)


# 条件值里出现这些词，说明是“是谁/多少”的提问，不是筛选值。
_STOP_VALUES = ("谁", "什么", "多少", "哪", "哪儿", "哪里", "哪些", "几",
                "几个", "怎么样", "如何", "为什么", "何时")


def _apply_conditions(rows: list[dict], headers: list[str], q: str):
    """应用限定条件：薪酬区间、列名是/为X、学历关键词、在X地点。

    返回 (过滤后的行, 是否应用了条件, 不能当定位键的列)。
    例如“薪酬5000以上”里的 5000 是阈值，不是某一行的单元格值，必须把
    薪酬列从“按值定位”里排除，否则只会命中恰好写着 5000 的那一行。
    English: Apply constraints and return (rows, applied, excluded columns).
    A number inside “salary >= 5000” is a threshold, not a cell selector, so the
    salary column must be excluded from value-based row selection."""
    applied = False
    out = rows
    exclude: set[str] = set()
    salary = next((h for h in headers if _header_concept(h) == "薪酬待遇"), "")
    match = re.search(r"(\d{2,6})\s*(?:元)?\s*(以上|以下|及以上|及以下|起|以内)", q)
    if match and salary:
        limit = float(match.group(1))
        above = match.group(2) in ("以上", "及以上", "起")

        def _ok(row):
            bounds = _salary_bounds(row.get(salary, ""))
            if not bounds:
                return False
            low, high = bounds
            return high >= limit if above else low <= limit

        out = [row for row in out if _ok(row)]
        applied = True
        exclude.add(salary)
    for header in headers:
        concept = _header_concept(header)
        if not concept:
            continue
        for alias in _COLUMN_SYNONYMS[concept]:
            match = re.search(
                re.escape(alias) + r"(?:是|为|：|:)\s*([^，,。；;\s]{1,30})", q)
            if match:
                value = match.group(1)
                if value.startswith(_STOP_VALUES):
                    continue
                out = [row for row in out if value in (row.get(header) or "")]
                applied = True
                exclude.add(header)
                break
    edu = next((h for h in headers if _header_concept(h) == "学历要求"), "")
    if edu:
        for token in ("博士", "硕士", "研究生", "本科", "大专"):
            if token in q and any(token in (row.get(edu) or "") for row in rows):
                out = [row for row in out if token in (row.get(edu) or "")]
                applied = True
                exclude.add(edu)
                break
    loc = next((h for h in headers if _header_concept(h) == "工作地点"), "")
    if loc:
        # 只认“在X的/在X有哪些”，避免把标题里的“职在郫都 筑梦未来”当条件。
        for token in re.findall(r"在([^，,。；;\s]{2,12})(?:的|有哪些)", q):
            if any(token in (row.get(loc) or "") for row in rows):
                out = [row for row in out if token in (row.get(loc) or "")]
                applied = True
                exclude.add(loc)
    return out, applied, exclude


def _select_rows(rows: list[dict], q: str, exclude: set[str] | None = None):
    """按单元格值定位行：同名多行全保留，前缀只取最长命中。"""
    exclude = exclude or set()
    candidates: list[tuple[dict, list[str]]] = []
    for row in rows:
        hits = [v for k, v in row.items()
                if k not in _META_KEYS and k not in exclude
                and _is_selector(v) and v.lower() in q]
        if hits:
            candidates.append((row, hits))
    if not candidates:
        return [], []
    seen: set[tuple] = set()
    unique: list[tuple[dict, list[str]]] = []
    for row, hits in candidates:
        key = _row_content_key(row)
        if key in seen:
            continue
        seen.add(key)
        unique.append((row, hits))
    best_len = max(max(len(v) for v in hits) for _, hits in unique)
    unique = [(row, hits) for row, hits in unique
              if max(len(v) for v in hits) == best_len]
    return [row for row, _ in unique], [v for _, hits in unique for v in hits]


def match_table_rows(rows: list[dict], question: str):
    """兼容入口：返回 (候选行, 列名命中, 命中值) 或 None。"""
    q = (question or "").strip().lower()
    if not q or not rows:
        return None
    rows = _filter_by_sheet(rows, q)
    headers = _rows_headers(rows)
    concepts = _question_concepts(q)
    header_hits = [h for h in headers
                   if h.lower() in q or _header_concept(h) in concepts]
    if not header_hits and not any(w in q for w in _INTENT_WORDS):
        return None
    filtered, conditions, exclude = _apply_conditions(rows, headers, q)
    selected, values = _select_rows(filtered, q, exclude)
    if not selected and not conditions:
        return None
    return selected or filtered, header_hits, values


def _format_list(rows: list[dict], column: str, question: str):
    """列表型答案：去重后按序号列出。"""
    values: list[str] = []
    seen: set[str] = set()
    for row in rows:
        value = (row.get(column) or "").strip()
        if value and value not in seen and not _is_formula(value):
            seen.add(value)
            values.append(value)
    if not values:
        return None
    concept = _header_concept(column)
    unit = {"企业名称": "家", "招聘岗位": "个", "联系电话": "个"}.get(concept, "条")
    sheet = rows[0].get(_SHEET_KEY, "")
    head = f"共 {len(values)} {unit}" + (f"（{sheet}）" if sheet else "")
    lines = [head]
    for i, value in enumerate(values[:80], 1):
        lines.append(f"{i}. {value}")
    if len(values) > 80:
        lines.append(f"……（共 {len(values)} 条，已列出前 80 条）")
    return "\n".join(lines), rows


def query_table(rows: list[dict], question: str):
    """确定性表格查询，返回 (answer_text, matched_rows) 或 None。

    v2.4.5：表头同义词、合并单元格续行、同名多行、按值反查、
    列表问题（有哪些企业/岗位）与限定条件（薪酬/学历/地点）。
    English: Deterministic table query returning (answer_text, matched_rows) or None.
    v2.4.5 adds header synonyms, merged-cell forward fill, multi-row entities,
    value-first lookup, list questions, and salary/education/location constraints."""
    q = (question or "").strip().lower()
    if not q or not rows:
        return None
    rows = _filter_by_sheet(rows, q)
    if not rows:
        return None
    headers = _rows_headers(rows)
    if not headers:
        return None
    concepts = _question_concepts(q)
    header_hits = [h for h in headers
                   if h.lower() in q or _header_concept(h) in concepts]
    if not header_hits and not any(w in q for w in _INTENT_WORDS):
        return None

    filtered, conditions, exclude = _apply_conditions(rows, headers, q)
    selected, matched_values = _select_rows(filtered, q, exclude)

    list_col = _list_column(headers, q)
    if list_col:
        base = selected
        if base and matched_values:
            # 列表问题只允许“公司/姓名/岗位”这类标识值收窄范围；
            # “职在郫都…”里恰好命中工作地点的“郫都”，不能把企业列表缩成 1 家。
            keep = []
            for row in base:
                for key, value in row.items():
                    if (key not in _META_KEYS and value in matched_values
                            and (key in _IDENTITY_KEYS
                                 or _header_concept(key) in _IDENTITY_CONCEPTS
                                 or _header_concept(key) == "招聘岗位")):
                        keep.append(row)
                        break
            base = keep or filtered
        if not base:
            base = filtered
        if base:
            return _format_list(base, list_col, q)

    # 多 sheet 同名实体：优先保留“问题点名的列确实有值”的行。
    # 问联系电话时，销售订单行没有该列，不应把整行倒出来。
    if selected and header_hits:
        def _usable(row, header):
            value = (row.get(header) or "").strip()
            return bool(value) and not _is_formula(value)

        scores = [sum(1 for h in header_hits if _usable(row, h))
                  for row in selected]
        best = max(scores)
        if best > 0:
            selected = [row for row, score in zip(selected, scores)
                        if score == best]
    if not selected:
        if not conditions:
            return None
        selected = filtered

    want_sum = any(w in q for w in _SUM_WORDS)
    want_avg = any(w in q for w in _AVG_WORDS)
    job_col = next((h for h in headers if _header_concept(h) == "招聘岗位"), "")

    def _label(row: dict) -> str:
        """行标识：公司/姓名优先；其次是岗位；最后才退回命中值。

        反查“电话=187…”或“5000-15000是哪个岗位”时，命中的是电话/薪酬，
        标签必须回到公司名；而“会计岗的招聘单位是谁”仍以“会计岗”开头。
        English: Prefer company/person identity, then job title, then matched
        values; reverse lookups by phone/salary/location must label the company."""
        matched_identity = ""
        matched_code = ""
        matched_job = ""
        for key, value in row.items():
            if key in _META_KEYS or value not in matched_values or _is_formula(value):
                continue
            concept = _header_concept(key)
            if key in _NAME_KEYS or concept in _IDENTITY_CONCEPTS:
                matched_identity = matched_identity or value
            elif key in _IDENTITY_KEYS:
                matched_code = matched_code or value
            elif concept == "招聘岗位":
                matched_job = matched_job or value
        if matched_identity:
            base = matched_identity
        elif matched_job:
            base = matched_job
        elif matched_code:
            base = matched_code
        else:
            base = ""
            for key, value in row.items():
                if (key in _NAME_KEYS and value and not _is_formula(value)
                        and _as_number(value) is None):
                    base = value
                    break
            if not base:
                for key, value in row.items():
                    concept = _header_concept(key)
                    if (key not in _META_KEYS and value and not _is_formula(value)
                            and _as_number(value) is None
                            and (concept in _IDENTITY_CONCEPTS
                                 or key in _IDENTITY_KEYS)):
                        base = value
                        break
            if not base:
                for value in row.values():
                    if (value in matched_values and _as_number(value) is None
                            and not re.search(r"\d", value)):
                        base = value
                        break
            if not base:
                for value in row.values():
                    if value and not _is_formula(value) and _as_number(value) is None:
                        base = value
                        break
            if not base:
                base = next((v for v in row.values()
                             if v in matched_values), "")
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

    def _row_cols(row: dict, label: str) -> list[str]:
        """输出列：问题点名的列；没有点名时才回退到整行。

        已经作为标签的值不重复输出；同名多行时自动补「招聘岗位」。"""
        sheet = row.get(_SHEET_KEY, "")
        base_label = (label[len(sheet) + 1:]
                      if sheet and label.startswith(sheet + " ") else label)
        explicit = [h for h in header_hits
                    if row.get(h) and not _is_formula(row.get(h))]
        if explicit:
            cols = [c for c in explicit if row.get(c) != base_label]
        else:
            cols = [k for k in row
                    if k not in _META_KEYS and row.get(k)
                    and not _is_formula(row.get(k))
                    and row.get(k) != base_label]
        if len(selected) > 1 and job_col and job_col not in cols and row.get(job_col):
            cols = [job_col] + cols
        return cols

    lines = []
    if want_sum or want_avg:
        for row in selected:
            label = _label(row)
            # 问题明确问「总分」且表里已有总分类：直接读该列，不重复求和
            if want_sum and "总分" in header_hits and row.get("总分"):
                lines.append(f"{label}：总分={row['总分']}")
                continue
            cols = _value_cols(row, header_hits)
            nums = [n for n in (_as_number(row[c]) for c in cols) if n is not None]
            if nums:
                total = sum(nums)
                if want_avg:
                    lines.append(f"{label}：平均={_fmt_number(total / len(nums))}")
                else:
                    lines.append(f"{label}：总分={_fmt_number(total)}")
        if not lines:
            return None
        return "\n".join(lines), selected

    # 同名多行按“标签 + 其余字段”分组：比亚迪 3 个岗位共享联系人和地点时，
    # 合成一块“招聘岗位：A / B / C”，不再把三行完整抄三遍。
    groups: list[dict] = []
    for row in selected:
        label = _label(row)
        cols = _row_cols(row, label)
        if not cols:
            formulas = [h for h, value in row.items()
                        if h not in _META_KEYS and _is_formula(value)
                        and (_header_base(h) in q or h.lower() in q)]
            if formulas:
                lines.append(f"{label}：{'、'.join(formulas)} 为公式，"
                             "文件未保存计算结果")
            continue
        job = ""
        if len(selected) > 1 and job_col and job_col in cols:
            job = (row.get(job_col) or "").strip()
        others = tuple((c, row.get(c, "")) for c in cols
                       if not (job and c == job_col))
        key = (label, others)
        group = next((g for g in groups if g["key"] == key), None)
        if group is None:
            group = {"key": key, "label": label, "others": others, "jobs": []}
            groups.append(group)
        if job and job not in group["jobs"]:
            group["jobs"].append(job)
    for index, group in enumerate(groups, 1):
        if not group["jobs"] and len(group["others"]) == 1:
            col, value = group["others"][0]
            line = f"{group['label']}：{col}={value}"
            if len(groups) > 1:
                line = f"{index}. {line}"
            lines.append(line)
            continue
        block = [group["label"]]
        if group["jobs"]:
            block.append("招聘岗位：" + " / ".join(group["jobs"]))
        block += [f"{col}：{value}" for col, value in group["others"]]
        if len(groups) > 1:
            block[0] = f"{index}. {group['label']}"
        lines.append("\n".join(block))
    if not lines:
        return None
    return "\n".join(lines), selected


def query_rows(rows: list[dict], question: str) -> str | None:
    """兼容入口：只返回答案文本（测试与旧调用方使用）。"""
    result = query_table(rows, question)
    return result[0] if result else None
