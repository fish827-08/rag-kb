"""数据模型：Record 与 Chroma metadata 互转（Chroma metadata 不支持 list，tags 逗号拼接）。"""
import math
from datetime import datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class RecordType(str, Enum):
    """来源类型。"""
    MEMORY = "memory"
    DOC_CHUNK = "doc_chunk"
    WEB_CHUNK = "web_chunk"


class Record(BaseModel):
    """记忆条目与文档 chunk 的统一模型。"""

    id: str = Field(default_factory=lambda: uuid4().hex)   # 主键：服务端生成，AI 不参与
    content: str
    type: RecordType = RecordType.MEMORY
    namespace: str = "default"
    # v3（2026-08-31）：client/project 仅审计归类与元数据展示，不参与隔离
    #（记忆与知识全共享）；agent_id 为兼容冗余（新写入时=project 或 "default"）
    agent_id: str = "default"
    client: str = "default"            # 来源客户端（框架自动识别：MCP clientInfo / REST HTTP / CLI）
    project: str = ""                  # 项目/任务归属（环境承载；仅审计归类）
    source: str | None = None
    tags: list[str] = Field(default_factory=list)
    importance: float = 0.5
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    # N21a/TASK-0067：访问元数据（A3 spec §2.2）
    access_count: int = 0                 # 被检索命中次数，0=从未命中
    last_accessed: str = ""               # 最近一次命中时间（ISO），空=从未命中

    def to_metadata(self) -> dict:
        """转 Chroma metadata；content 不入 metadata（它是 Chroma 的 document），tags 逗号拼接。"""
        return {
            "id": self.id,
            "type": self.type.value,
            "namespace": self.namespace,
            "agent_id": self.agent_id,
            "client": self.client,
            "project": self.project,
            "source": self.source,
            "tags": ",".join(self.tags),
            "importance": self.importance,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "access_count": self.access_count,
            "last_accessed": self.last_accessed,
        }

    @classmethod
    def from_chroma(cls, record_id: str, document: str, metadata: dict) -> "Record":
        """从 Chroma 的 id/document/metadata 还原；tags 按逗号拆分。
        旧记录缺失 access_count/last_accessed 时用 .get 默认值 0/""（向后兼容，无需迁移）；
        project 缺失回落 ""（默认桶），agent_id/client 缺失回落 "default"。"""
        return cls(
            id=record_id,
            content=document,
            type=RecordType(metadata.get("type", RecordType.MEMORY.value)),
            namespace=metadata.get("namespace", "default"),
            agent_id=metadata.get("agent_id", "default"),
            client=metadata.get("client", "default"),
            project=metadata.get("project", ""),
            source=metadata.get("source"),
            tags=[t for t in (metadata.get("tags", "") or "").split(",") if t],
            importance=metadata.get("importance", 0.5),
            created_at=metadata.get("created_at", ""),
            updated_at=metadata.get("updated_at", ""),
            access_count=metadata.get("access_count", 0),
            last_accessed=metadata.get("last_accessed", ""),
        )


def decay_factor(last_accessed: str, created_at: str, access_count: int,
                 lambda_: float = 0.02, gamma: float = 0.3,
                 now: datetime | None = None) -> float:
    """访问频率衰减因子（A3 spec §3.1，N21a 纯函数）。

    公式：decay_factor = exp(-λ * days_since_last_accessed) * (1 + γ * log₂(1 + access_count))
    - last_accessed 为空时用 created_at 替代（从未命中=创建时间）
    - 无效日期或无时间参考时返回 1.0（不衰减，容错）
    - 纯函数无副作用，N21b 负责将其集成进检索评分管道
    - now 可注入（2026-08-30 v2）：时钟注入验证——测试可拨到第 N 天验证衰减机理，
      生产默认 datetime.now()；遗忘机制验证见 USER_GUIDE 对应章节
    """
    ref = last_accessed if last_accessed else created_at
    if not ref:
        return 1.0
    try:
        ref_dt = datetime.fromisoformat(ref)
    except (ValueError, TypeError):
        return 1.0
    now = now or datetime.now()
    days = (now - ref_dt).total_seconds() / 86400
    if days < 0:
        days = 0.0  # 未来时间不衰减
    return math.exp(-lambda_ * days) * (1 + gamma * math.log2(1 + access_count))