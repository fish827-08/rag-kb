"""文档摄取：按扩展名解析为纯文本 + 递归切分（设计文档第 9 节）。

解析分派：.pdf→pypdf；.docx→python-docx；.md/.txt→直读；
Office 等已知格式（.xlsx/.pptx/...）→ markitdown 兜底；
白名单之外的扩展名一律抛 UnsupportedFormatError（API 层转 400）。

重依赖（pypdf / python-docx / markitdown / langchain-text-splitters）
均在函数内延迟导入，服务启动不加载。
"""
from pathlib import Path

# 直接解析的扩展名（.md / .txt 直读原文，不做任何清洗）
_DIRECT_EXTS = {".md", ".txt"}
# markitdown 兜底的已知格式（Office 全格式等；markitdown 对未知扩展名
# 会当纯文本静默返回，故必须白名单控制，白名单外直接拒绝）
_MARKITDOWN_EXTS = {".xlsx", ".xls", ".pptx", ".ppt", ".csv", ".json",
                    ".xml", ".html", ".htm", ".rst"}


class UnsupportedFormatError(Exception):
    """不支持的文档格式（API 层转 400）。"""


def parse_file(path: Path | str) -> str:
    """解析文档为纯文本；不支持的扩展名抛 UnsupportedFormatError。"""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in _DIRECT_EXTS:
        return path.read_text(encoding="utf-8")
    if ext == ".pdf":
        return _parse_pdf(path)
    if ext == ".docx":
        return _parse_docx(path)
    if ext in _MARKITDOWN_EXTS:
        return _parse_markitdown(path)
    raise UnsupportedFormatError(f"不支持的文档格式：{ext or '<无扩展名>'}（{path.name}）")


def _parse_pdf(path: Path) -> str:
    """pypdf 逐页提取文本，空页跳过。"""
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _parse_docx(path: Path) -> str:
    """python-docx 提取全部段落文本。"""
    from docx import Document
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)


def _parse_markitdown(path: Path) -> str:
    """markitdown 兜底解析（Office 全格式）；解析失败抛 UnsupportedFormatError。"""
    try:
        from markitdown import MarkItDown
        return MarkItDown().convert(str(path)).text_content
    except Exception as exc:
        raise UnsupportedFormatError(f"文档解析失败（{path.name}）：{exc}") from exc


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """langchain RecursiveCharacterTextSplitter 递归切分（按分隔符层级回退）。"""
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    splitter = RecursiveCharacterTextSplitter(chunk_size=size,
                                              chunk_overlap=overlap)
    return splitter.split_text(text)
