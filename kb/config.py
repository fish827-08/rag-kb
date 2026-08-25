"""全局配置：环境变量前缀 KB_，支持 .env 文件。"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全部配置；环境变量前缀 KB_，支持 .env 文件。"""

    model_config = SettingsConfigDict(
        env_prefix="KB_", env_file=".env", extra="ignore"
    )

    data_dir: Path = Path("kb_data")            # 运行数据根目录
    device: str = ""                            # 空=自动检测；显式设 cpu/cuda 覆盖
    embed_model: str = "BAAI/bge-m3"
    llm_mode: str = "auto"                      # local | auto | cloud
    ollama_base_url: str = "http://localhost:11434"
    llm_model: str = "qwen3:4b"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    chunk_size: int = 500
    chunk_overlap: int = 100
    watch_dir: Path = Path("data")            # 目录监听（serve 挂载）；空串/"."=不启动
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    context_token_limit: int = 2000             # /ask 检索上下文 token 硬上限
    llm_max_tokens: int = 800
    llm_temperature: float = 0.2
    cache_size: int = 100                       # /ask 答案缓存条数（LRU）
    cache_sim_threshold: float = 0.95           # 缓存命中相似度阈值
    sensitive_namespaces: str = ""              # 逗号分隔，敏感 namespace 强制本地
    # ---- 日志（日志设计文档第 3 节，N17）----
    log_level: str = "INFO"                     # 全局级别（DEBUG 开发期排查用）
    log_dir: Path = Path("logs")                # 日志目录（相对仓库根；gitignore 排除）
    log_max_bytes: int = 1048576                # 单文件 1MB，超限轮转
    log_backup_count: int = 5                   # 轮转保留备份数（合计 ≤6MB）

    @property
    def chroma_dir(self) -> Path:
        """ChromaDB 持久化目录。"""
        return self.data_dir / "chroma"

    @property
    def runtime_file(self) -> Path:
        """运行时选择持久化文件（如 GPU 加速选择）。"""
        return self.data_dir / "runtime.json"

    @property
    def sensitive_ns_list(self) -> list[str]:
        """把敏感 namespace 拆分为列表，空串→[]。"""
        return [s.strip() for s in self.sensitive_namespaces.split(",") if s.strip()]

    @property
    def log_file(self) -> Path:
        """日志文件路径（log_dir/kb.log）。"""
        return self.log_dir / "kb.log"


@lru_cache
def get_settings() -> Settings:
    """全局配置单例；测试用环境变量隔离时调用 get_settings.cache_clear()。"""
    return Settings()