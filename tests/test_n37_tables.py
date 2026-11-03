"""v2.4 表格导入与精准问答（设计：docs/superpowers/specs/2026-11-03-kb-v2.4-design.md）。

覆盖：
1) CSV 逐行转 `列名=值 | ...` 文本并打 table 标签；
2) 确定性表格问答可直接命中单元格（无需 LLM）；
3) 总分/平均聚合；
4) 未命中时返回 None（交回原 RAG 流程）。
"""
import pytest

from kb.tables import (parse_row_text, parse_table_file, query_rows,
                       row_to_text, rows_from_content)


def _write_csv(path, text):
    path.write_text(text, encoding="utf-8")
    return path


class TestTableParsing:
    """表格解析纯函数。"""

    def test_CSV解析表头与数据行(self, tmp_path):
        p = _write_csv(tmp_path / "成绩.csv",
                       "姓名,语文,数学\n小明,95,88\n小红,90,92\n")
        tables = parse_table_file(p)
        assert len(tables) == 1
        assert tables[0]["headers"] == ["姓名", "语文", "数学"]
        assert tables[0]["rows"] == [["小明", "95", "88"], ["小红", "90", "92"]]

    def test_行文本往返解析(self):
        text = row_to_text(["姓名", "语文", "数学"], ["小明", "95", ""])
        assert text == "姓名=小明 | 语文=95"
        parsed = parse_row_text(text)
        assert parsed == {"姓名": "小明", "语文": "95"}

    def test_GBK编码CSV(self, tmp_path):
        p = tmp_path / "gbk.csv"
        p.write_bytes("姓名,语文\n小明,95\n".encode("gbk"))
        tables = parse_table_file(p)
        assert tables[0]["headers"] == ["姓名", "语文"]
        assert tables[0]["rows"] == [["小明", "95"]]

    def test_XLSX解析多sheet(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "成绩"
        ws.append(["姓名", "语文", "数学"])
        ws.append(["小明", 95, 88])
        ws2 = wb.create_sheet("汇总")
        ws2.append(["班级", "人数"])
        ws2.append(["一班", 30])
        p = tmp_path / "成绩.xlsx"
        wb.save(p)
        tables = parse_table_file(p)
        assert [t["sheet"] for t in tables] == ["成绩", "汇总"]
        assert tables[0]["rows"] == [["小明", "95", "88"]]
        assert tables[1]["rows"] == [["一班", "30"]]

    def test_空表抛格式错误(self, tmp_path):
        from kb.ingest import UnsupportedFormatError
        p = _write_csv(tmp_path / "空.csv", "姓名,语文\n")
        with pytest.raises(UnsupportedFormatError):
            parse_table_file(p)


class TestDeterministicQuery:
    """query_rows：按单元格值定位行、按列名筛选列。"""

    def test_命中姓名与列名(self):
        rows = rows_from_content(row_to_text(["姓名", "语文", "数学"],
                                             ["小明", "95", "88"]))
        assert query_rows(rows, "小明的语文成绩怎么样") == "小明：语文=95"

    def test_未命中返回None(self):
        rows = [{"姓名": "小明", "语文": "95"}]
        assert query_rows(rows, "小刚的语文成绩") is None

    def test_总分聚合(self):
        rows = [{"姓名": "小明", "语文": "95", "数学": "88"}]
        assert query_rows(rows, "小明的总分是多少") == "小明：总分=183"

    def test_平均聚合(self):
        rows = [{"姓名": "小明", "语文": "95", "数学": "88"}]
        answer = query_rows(rows, "小明的平均分")
        assert answer == "小明：平均=91.50"

    def test_平均排除学号与总分列(self):
        row = {"__sheet": "一班", "学号": "01001", "姓名": "小明",
               "语文": "95", "数学": "88", "总分": "183"}
        assert query_rows([row], "小明的平均分") == "一班 小明：平均=91.50"

    def test_短数字分数不误命中问题里的长数字(self):
        """分数 50 不能因子串出现在「1500」里而被当成定位键。"""
        rows = [{"学号": "01500", "姓名": "张三", "数学": "50"}]
        assert query_rows(rows, "1500号学生的数学成绩") is None

    def test_无关问题不劫持表格(self):
        """「北京有哪些旅游景点」没有列名/意图词 → 不能命中城市表。"""
        rows = [{"城市": "北京", "人口": "2189", "面积": "16410"}]
        assert query_rows(rows, "北京有哪些旅游景点") is None

    def test_前缀值只取最长命中(self):
        rows = [{"用户": "用户5", "分数": "10"},
                {"用户": "用户55", "分数": "20"},
                {"用户": "用户550", "分数": "30"}]
        assert query_rows(rows, "用户550的分数") == "用户550：分数=30"

    def test_重复内容行只答一次(self):
        rows = [{"城市": "北京", "人口": "2189"},
                {"城市": "北京", "人口": "2189"}]
        assert query_rows(rows, "北京的人口是多少") == "北京：人口=2189"

    def test_多sheet按表名消歧(self):
        rows = [
            {"__sheet": "五班", "姓名": "张芳", "数学": "99"},
            {"__sheet": "十五班", "姓名": "张芳", "数学": "61"},
        ]
        assert query_rows(rows, "五班张芳的数学成绩") == "五班 张芳：数学=99"
        # 「五班」⊂「十五班」：最长匹配优先，避免问十五班命中五班
        assert query_rows(rows, "十五班张芳的数学成绩") == "十五班 张芳：数学=61"

    def test_多sheet未指定表名时标注表名(self):
        rows = [
            {"__sheet": "一班", "姓名": "王艳", "语文": "51"},
            {"__sheet": "二班", "姓名": "王艳", "语文": "80"},
        ]
        answer = query_rows(rows, "王艳的语文成绩")
        assert "一班 王艳：语文=51" in answer
        assert "二班 王艳：语文=80" in answer


@pytest.mark.integration
class TestTableService:
    """服务层：入库后 /ask 直接返回表格答案（无 LLM）。"""

    def test_CSV入库并精准回答(self, env_isolated, tmp_path):
        from kb.service import KBService
        p = _write_csv(tmp_path / "成绩表.csv",
                       "姓名,语文,数学,英语\n小明,95,88,92\n小红,90,91,89\n")
        svc = KBService()
        r = svc.add_document(p, project="school")
        assert r["chunks"] == 2  # 两行数据，一行一条记录

        hits = svc.search("小明 语文", project="school")
        assert any(h["source"] == "成绩表.csv" for h in hits)

        ans = svc.ask("小明的语文成绩怎么样", project="school")
        assert ans["answer"] == "小明：语文=95"
        assert ans["llm"] == "table"
        assert ans["table"] is True
        assert ans["sources"] and ans["sources"][0]["source"] == "成绩表.csv"

    def test_表格记录带table标签(self, env_isolated, tmp_path):
        from kb.service import KBService
        p = _write_csv(tmp_path / "表.csv", "姓名,分数\n小明,95\n")
        svc = KBService()
        svc.add_document(p)
        records = list(svc.scopes.get("").store.iter_all())
        assert len(records) == 1
        assert "table" in records[0].tags
        assert records[0].type.value == "doc_chunk"


class TestRealWorldSheets:
    """真实业务表：标题行、合并单元格、同义词、列表、反查与限定条件。"""

    def test_标题行不影响表头识别(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["2026年招聘岗位表"])
        ws.append(["序号", "企业名称", "招聘岗位", "薪酬待遇（元/月）"])
        ws.append(["1", "A公司", "会计", "5000-8000"])
        p = tmp_path / "有标题.xlsx"
        wb.save(p)
        tables = parse_table_file(p)
        assert tables[0]["headers"] == ["序号", "企业名称", "招聘岗位",
                                        "薪酬待遇（元/月）"]
        assert tables[0]["rows"] == [["1", "A公司", "会计", "5000-8000"]]

    def test_合并单元格续行补全(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["企业名称", "招聘岗位", "联系电话", "工作地点"])
        ws.append(["A公司", "岗位一", "13800000000", "郫都区"])
        ws.append(["", "岗位二", "", ""])
        p = tmp_path / "合并.xlsx"
        wb.save(p)
        tables = parse_table_file(p)
        assert tables[0]["rows"][1][0] == "A公司"
        assert tables[0]["rows"][1][2] == "13800000000"
        assert tables[0]["rows"][1][3] == "郫都区"

    def test_xls解析走xlrd(self, tmp_path, monkeypatch):
        """用假 xlrd 验证 .xls 分支的取行与表头归一（真实 .xls 另行实测）。"""
        import types
        from kb import tables

        data = [["附件"], ["招聘岗位表"],
                ["序号", "招聘单位", "招聘岗位", "薪酬待遇"],
                ["1", "A集团", "会计岗", "4.3万元/年"]]

        class _Cell:
            def __init__(self, value):
                self.value = value
                self.ctype = 1

        class _Sheet:
            name = "Sheet1"
            nrows = len(data)
            ncols = max(len(row) for row in data)

            def cell(self, r, c):
                row = data[r]
                return _Cell(row[c] if c < len(row) else "")

        class _Book:
            datemode = 0

            def sheets(self):
                return [_Sheet()]

            def release_resources(self):
                pass

        fake = types.SimpleNamespace(
            XL_CELL_DATE=3, open_workbook=lambda *a, **k: _Book(),
            xldate_as_datetime=lambda v, d: None)
        monkeypatch.setattr(tables, "_ingest_import", lambda *a: fake)
        p = tmp_path / "old.xls"
        p.write_bytes(b"fake")
        result = parse_table_file(p)
        assert result[0]["headers"] == ["序号", "招聘单位", "招聘岗位", "薪酬待遇"]
        assert result[0]["rows"] == [["1", "A集团", "会计岗", "4.3万元/年"]]

    def test_薪资待遇命中薪酬待遇并只输出该列(self):
        rows = [{"企业名称": "A公司", "招聘岗位": "销售",
                 "薪酬待遇（元/月）": "5000-15000", "工作地点": "郫都区"}]
        answer = query_rows(rows, "A公司的薪资待遇")
        assert "薪酬待遇（元/月）=5000-15000" in answer
        assert "工作地点" not in answer

    def test_同名多行合并岗位(self):
        rows = [
            {"企业名称": "A公司", "招聘岗位": "岗位一",
             "联系电话": "13800000000", "工作地点": "郫都区"},
            {"企业名称": "A公司", "招聘岗位": "岗位二",
             "联系电话": "13800000000", "工作地点": "郫都区"},
        ]
        answer = query_rows(rows, "A公司的联系电话和工作地点")
        assert "岗位一 / 岗位二" in answer
        assert answer.count("联系电话：13800000000") == 1
        assert "工作地点：郫都区" in answer

    def test_按电话反查公司(self):
        rows = [{"企业名称": "A公司", "招聘岗位": "客服",
                 "联系电话": "13800000000"}]
        answer = query_rows(rows, "13800000000是谁的电话")
        assert "A公司" in answer
        assert "联系电话=13800000000" in answer

    def test_列表问题列出企业(self):
        rows = [
            {"__sheet": "8月第2期", "企业名称": "A公司", "招聘岗位": "会计"},
            {"__sheet": "8月第2期", "企业名称": "B公司", "招聘岗位": "销售"},
        ]
        answer = query_rows(rows, "8月第2期有哪些企业")
        assert answer.splitlines()[0] == "共 2 家（8月第2期）"
        assert "1. A公司" in answer and "2. B公司" in answer

    def test_表名里的岗位不抢列表目标(self):
        rows = [
            {"__sheet": "8月第2期", "企业名称": "A公司", "招聘岗位": "会计"},
            {"__sheet": "8月第2期", "企业名称": "B公司", "招聘岗位": "销售"},
        ]
        answer = query_rows(rows, "大学生就业岗位清单有哪些企业")
        assert "共 2 家" in answer
        assert "A公司" in answer

    def test_薪酬下限筛选(self):
        rows = [
            {"__sheet": "8月第2期", "企业名称": "A公司", "薪酬待遇（元/月）": "5000-15000"},
            {"__sheet": "8月第2期", "企业名称": "B公司", "薪酬待遇（元/月）": "3000-4000"},
            {"__sheet": "8月第2期", "企业名称": "C公司", "薪酬待遇（元/月）": "面议"},
        ]
        answer = query_rows(rows, "薪酬5000以上的企业有哪些")
        assert "A公司" in answer and "B公司" not in answer and "C公司" not in answer

    def test_中文表名匹配阿拉伯数字表名(self):
        rows = [
            {"__sheet": "8月第2期", "企业名称": "A公司", "招聘岗位": "会计"},
            {"__sheet": "7月第1期", "企业名称": "B公司", "招聘岗位": "销售"},
        ]
        answer = query_rows(rows, "（八月份第2期）有哪些企业")
        assert "A公司" in answer and "B公司" not in answer
