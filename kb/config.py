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
    api_key: str = ""                            # KB_API_KEY：空=不鉴权（本地回环零摩擦）；非空=启用 Bearer/X-API-Key 鉴权（N19/TASK-0062）
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
    # ---- 本地监控 Agent（0015 设计书，N18；TASK-0021 去常驻改按需）----
    monitor_enabled: bool = False               # 默认不启常驻线程（去常驻：按需端点/看板按钮触发）
    monitor_interval: int = 10                  # 轮询间隔（分钟），≥1，非法回退默认（常驻模式用）
    monitor_startup_run: bool = True            # 启动时立即跑一轮（便于验证；常驻模式用）
    monitor_max_tokens: int = 300               # 摘要输出上限（护栏：≤300，硬约束）
    monitor_autotimer: int = 0                  # KB_MONITOR_AUTOTIMER：看板前端自动轮询间隔（分钟），0=关
    dispatch_enabled: bool = True                # KB_DISPATCH_ENABLED：监控单轮后异常调度（comm:dispatch），默认开（TASK-0049）
    monitor_llm: str = "off"                     # KB_MONITOR_LLM：监控摘要模式 off=全程纯文本不调LLM(默认零成本/不抢GPU) / auto=LLM可用摘要化不可用降级(TASK-0065)
    # ---- 记忆治理 A3（N21b 衰减评分公式模块，TASK-0068）----
    decay_enabled: bool = False                   # KB_DECAY_ENABLED：访问频率衰减开关，默认关（零行为变化）
    decay_lambda: float = 0.02                    # KB_DECAY_LAMBDA：衰减速率 λ（/天），半衰期≈35天
    decay_gamma: float = 0.3                      # KB_DECAY_GAMMA：高频访问加权系数 γ，access_count=10→约2.0倍
    # ---- 记忆治理 A3（N22b 新鲜度权重，TASK-0070）----
    freshness_enabled: bool = False               # KB_FRESHNESS_ENABLED：新鲜度权重开关，默认关（零行为变化）
    freshness_beta: float = 0.05                  # KB_FRESHNESS_BETA：新鲜度衰减速率 β（/天），半衰期≈14天
    freshness_alpha: float = 0.3                  # KB_FRESHNESS_ALPHA：新鲜度加权上限系数 α，boost范围[1,1.3]
    dashboard_autoopen: bool = False            # serve 启动自动打开看板（默认关：用户主动访问，防骚扰）
    dashboard_url: str = "http://127.0.0.1:8000/dashboard/"  # 看板地址（可覆盖）

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