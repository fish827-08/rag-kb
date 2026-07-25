# -*- coding: utf-8 -*-
"""
@Time ： 2026/7/14 下午 10:01
@Auth ： Yu
@File ：config.py
@IDE ：PyCharm
@Intro : 配置模块
"""

import os

from dotenv import load_dotenv
from pathlib import Path

load_dotenv()


class Config:
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未设置，请检查 .env 文件")
    DEEPSEEK_MODEL = "deepseek-v4-flash"
    EMBEDDING_MODEL = "BAAI/bge-m3"
    EMBEDDING_DEVICE = "cpu"
    CHUNK_SIZE = 500  # 切分大小
    CHUNK_OVERLAP = 100  # 重叠大小
    SEARCH_K = 3  # 检索返回数量
    BASE_DIR = Path(__file__).resolve().parent.parent  # 项目根目录 resolve()规范化，补齐目录去掉./  Path(__file__)获取当前文件路径
    DATA_DIR = BASE_DIR / "data"  # 数据目录
    CHROMA_DIR = BASE_DIR / "chroma_db"  # 向量库目录
    COLLECTION_NAME = "rag_kb_collection"  # ChromaDB collection
    # 系统提示词
    SYSTEM_PROMPT = \
        "你是一个文档问答助手。请根据以下检索到的文档内容回答用户问题。" \
        "如果文档中没有相关信息，请说'文档中没有相关信息'。" \
        "如果文档中有相关信息，请标注信息来源。" \
        "\n\n文档内容:\n{context}"

    # ---- 遍历相关方法 ----
    @classmethod
    def get_all(cls) -> dict:
        """返回所有配置的字典（过滤掉内置属性和方法）"""
        return {
            k: v
            for k, v in vars(cls).items()
            if not k.startswith(("_", "get")) and not callable(v)
        }

    @classmethod
    def get_keys(cls):
        """返回所有配置的键"""
        return [
            k for k, v in vars(cls).items()
            if not k.startswith(("_", "get")) and not callable(v)
        ]

    @classmethod
    def get_values(cls):
        """返回所有配置的值"""
        return [
            v for k, v in vars(cls).items()
            if not k.startswith(("_", "get")) and not callable(v)
        ]


# —— 关键：兼容旧写法 config.DEBUG ——
def __getattr__(name):
    try:
        return getattr(Config, name)
    except AttributeError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from None


if __name__ == "__main__":
    print("BASE_DIR:" + str(Config.BASE_DIR))
    print("DATA_DIR:" + str(Config.DATA_DIR))
