"""kb REST HTTP 客户端（TASK-0028 包化①：从 board.py 机械搬移）。

仅标准库 urllib；依赖方向单向：board.py → 各模块 → client.py，
本模块是依赖最底层，不 import orchestra 其他模块。
"""

import json
import urllib.error
import urllib.request

KB_BASE = "http://127.0.0.1:8000/api/v1"


class BoardUnavailable(Exception):
    """kb 服务不可达或服务端错误。"""


def _request(method: str, path: str, body: dict | None = None) -> dict:
    """kb REST 请求封装。

    连接失败/5xx → BoardUnavailable（退出码 2）；
    4xx → RuntimeError（退出码 1，提示调用方参数或状态问题）。
    """
    url = f"{KB_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            raise BoardUnavailable(f"kb 服务错误 HTTP {e.code}") from e
        detail = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"kb 拒绝请求 HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise BoardUnavailable(f"kb 服务不可达：{e}") from e
