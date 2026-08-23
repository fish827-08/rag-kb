"""数据模型：Record 与 Chroma metadata 互转（Chroma metadata 不支持 list，tags 逗号拼接）。"""
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

    id: str = Field(default_factory=lambda: uuid4().hex)
    content: str
    type: RecordType = RecordType.MEMORY
    namespace: str = "default"
    source: str | None = None
    tags: list[str] = Field(default_factory=list)
    importance: float = 0.5
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    def to_metadata(self) -> dict:
        """转 Chroma metadata；content 不入 metadata（它是 Chroma 的 document），tags 逗号拼接。"""
        return {
            "id": self.id,
            "type": self.type.value,
            "namespace": self.namespace,
            "source": self.source,
            "tags": ",".join(self.tags),
            "importance": self.importance,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_chroma(cls, record_id: str, document: str, metadata: dict) -> "Record":
        """从 Chroma 的 id/document/metadata 还原；tags 按逗号拆分。"""
        return cls(
            id=record_id,
            content=document,
            type=RecordType(metadata.get("type", RecordType.MEMORY.value)),
            namespace=metadata.get("namespace", "default"),
            source=metadata.get("source"),
            tags=[t for t in (metadata.get("tags", "") or "").split(",") if t],
            importance=metadata.get("importance", 0.5),
            created_at=metadata.get("created_at", ""),
            updated_at=metadata.get("updated_at", ""),
        )