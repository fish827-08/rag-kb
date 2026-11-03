"""文档/网页摄取：解析为纯文本 + 递归切分（设计文档第 9 节）。

解析分派：.pdf→pypdf；.docx→python-docx；.md/.txt→直读；
Office 等已知格式（.xlsx/.pptx/...）→ markitdown 兜底；
白名单之外的扩展名一律抛 UnsupportedFormatError（API 层转 400）。

网页摄取：httpx 拉取（超时 15s，UA 伪装常规浏览器）→ trafilatura
提取正文；抓取/提取失败抛 WebFetchError（API 层转 400 + 原因）。

重依赖（pypdf / python-docx / markitdown / langchain-text-splitters /
httpx / trafilatura）均在函数内延迟导入，服务启动不加载；其中
pypdf/python-docx/markitdown/trafilatura 属于可选 extra `[ingest]`，
缺失时返回带安装指引的可读错误。
"""
import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

_INGEST_HINT = "请运行 pip install \"kb-memory[ingest]\" 后重试"
_MAX_WEB_BYTES = 5 * 1024 * 1024  # 网页正文原始 HTML 上限 5MB


def _ingest_import(module: str, what: str):
    """导入摄取可选依赖；缺失时抛带 extra 安装指引的 UnsupportedFormatError。"""
    import importlib
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise UnsupportedFormatError(
            f"{what}需要可选依赖 {module}：{_INGEST_HINT}") from exc

# 直接解析的扩展名（.md / .txt 直读原文，不做任何清洗）
_DIRECT_EXTS = {".md", ".txt"}
# markitdown 兜底的已知格式（Office 全格式等；markitdown 对未知扩展名
# 会当纯文本静默返回，故必须白名单控制，白名单外直接拒绝）
_MARKITDOWN_EXTS = {".xlsx", ".xls", ".pptx", ".ppt", ".csv", ".json",
                    ".xml", ".html", ".htm", ".rst"}


class UnsupportedFormatError(Exception):
    """不支持的文档格式（API 层转 400）。"""


class WebFetchError(Exception):
    """网页抓取或正文提取失败（API 层转 400 + 原因）。"""


# 网页抓取 UA：伪装常规浏览器（设计文档第 9 节），避免站点拒绝默认 UA
_WEB_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36")


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
    PdfReader = _ingest_import("pypdf", "解析 PDF ").PdfReader
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _parse_docx(path: Path) -> str:
    """python-docx 提取全部段落文本。"""
    Document = _ingest_import("docx", "解析 DOCX ").Document
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)


def _parse_markitdown(path: Path) -> str:
    """markitdown 兜底解析（Office 全格式）；解析失败抛 UnsupportedFormatError。"""
    MarkItDown = _ingest_import("markitdown", "解析 Office 文档 ").MarkItDown
    try:
        return MarkItDown().convert(str(path)).text_content
    except Exception as exc:
        raise UnsupportedFormatError(f"文档解析失败（{path.name}）：{exc}") from exc


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """langchain RecursiveCharacterTextSplitter 递归切分（按分隔符层级回退）。"""
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    splitter = RecursiveCharacterTextSplitter(chunk_size=size,
                                              chunk_overlap=overlap)
    return splitter.split_text(text)


def _validate_public_url(url: str) -> None:
    """网页入库 SSRF 防护：仅 http(s)，拒绝回环/私有/链路本地/保留地址。

    本地调试可设 KB_WEB_ALLOW_PRIVATE=1 放行（默认关闭）。
    English: SSRF guard for web ingestion: only http(s); reject loopback, private,
    link-local and reserved addresses. Set KB_WEB_ALLOW_PRIVATE=1 for local testing."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebFetchError(f"仅支持 http(s) 网页：{url}")
    host = parsed.hostname
    if not host:
        raise WebFetchError(f"URL 缺少主机名：{url}")
    if os.environ.get("KB_WEB_ALLOW_PRIVATE", "").strip().lower() in (
            "1", "true", "yes"):
        return
    try:
        infos = socket.getaddrinfo(host, parsed.port or 80,
                                   type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebFetchError(f"域名解析失败（{host}）：{exc}") from exc
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            raise WebFetchError(f"URL 指向私有/回环地址，已拒绝：{url}")


def _fetch_html(url: str) -> str:
    """httpx 抓取页面 HTML：超时 15s、UA 伪装浏览器、手动跟随最多 3 次重定向，
    每一跳都做 SSRF 校验，并限制响应体大小（5MB）。
    English: Fetch HTML with a 15s timeout, browser UA, up to 3 manual redirects
    (each hop validated for SSRF), and a 5MB response-size cap."""
    import httpx
    current = url
    with httpx.Client(timeout=15, headers={"User-Agent": _WEB_USER_AGENT},
                      follow_redirects=False) as client:
        for _ in range(4):
            _validate_public_url(current)
            with client.stream("GET", current) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        raise WebFetchError(f"重定向缺少 Location：{current}")
                    current = str(httpx.URL(current).join(location))
                    continue
                resp.raise_for_status()
                data = bytearray()
                for chunk in resp.iter_bytes():
                    data.extend(chunk)
                    if len(data) > _MAX_WEB_BYTES:
                        raise WebFetchError(
                            f"网页响应体超过 {_MAX_WEB_BYTES} 字节上限")
                return bytes(data).decode(resp.encoding or "utf-8",
                                          errors="replace")
        raise WebFetchError(f"重定向次数过多：{url}")


def fetch_webpage(url: str) -> str:
    """抓取网页并提取正文：_fetch_html 拉取 → trafilatura.extract 提取；
    抓取异常或正文提取结果为空时抛 WebFetchError。"""
    try:
        html = _fetch_html(url)
    except Exception as exc:
        raise WebFetchError(f"网页抓取失败（{url}）：{exc}") from exc
    try:
        trafilatura = _ingest_import("trafilatura", "提取网页正文 ")
    except UnsupportedFormatError as exc:
        raise WebFetchError(str(exc)) from exc
    text = trafilatura.extract(html)
    if not text:
        raise WebFetchError(f"网页正文提取失败（{url}）：未识别到正文内容")
    return text
