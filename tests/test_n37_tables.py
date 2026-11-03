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
