"""kb REST HTTP 客户端（TASK-0028 包化①：从 board.py 机械搬移）。

仅标准库 urllib；依赖方向单向：board.py → 各模块 → client.py，
本模块是依赖最底层，不 import orchestra 其他模块。
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

KB_BASE = "http://127.0.0.1:8000/api/v1"


def _load_api_key(env_file: Path | None = None) -> str:
    """读 KB_API_KEY（N20 客户端适配）：环境变量优先，其次最小解析仓库根 .env。

    不 import kb 包（保持本模块零依赖最底层）；返回空串 = 不鉴权，
    与 N19 前行为完全一致（本地回环零摩擦）。真实 key 不落仓，只在本机。
    """
    key = os.environ.get("KB_API_KEY", "")
    if key.strip():
        return key.strip()
    if env_file is None:
        env_file = Path(__file__).resolve().parent.parent / ".env"
    try:
        if not env_file.is_file():
            return ""
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() == "KB_API_KEY":
                return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


class BoardUnavailable(Exception):
    """kb 服务不可达或服务端错误。"""


def _request(method: str, path: str, body: dict | None = None) -> dict:
    """kb REST 请求封装。

    连接失败/5xx → BoardUnavailable（退出码 2）；
    4xx → RuntimeError（退出码 1，提示调用方参数或状态问题）；
    401 额外提示检查 KB_API_KEY（N20：服务端启用鉴权而客户端未配/不匹配）。
    """
    url = f"{KB_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    api_key = _load_api_key()
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise RuntimeError(
                "kb 鉴权失败 HTTP 401：API Key 未配置或不匹配，检查 KB_API_KEY"
                "（环境变量或仓库根 .env，见 docs/USER_GUIDE.md §5.1）") from e
        if e.code >= 500:
            raise BoardUnavailable(f"kb 服务错误 HTTP {e.code}") from e
        detail = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"kb 拒绝请求 HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise BoardUnavailable(f"kb 服务不可达：{e}") from e
