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

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = "deepseek-v4-flash"
EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_DEVICE = "cpu"
CHUNK_SIZE = 500  # 切分大小
CHUNK_OVERLAP = 100  # 重叠大小
SEARCH_K = 3  # 检索返回数量
BASE_DIR = Path.cwd().parent  # 项目根目录
DATA_DIR = BASE_DIR / "data"  # 数据目录
CHROMA_DIR = BASE_DIR / "chroma_db"  # 向量库目录
COLLECTION_NAME = "rag_kb_collection"  # ChromaDB collection
# 系统提示词
SYSTEM_PROMPT = \
    "你是一个文档问答助手。请根据以下检索到的文档内容回答用户问题。" \
    "如果文档中没有相关信息，请说'文档中没有相关信息'。" \
    "如果文档中有相关信息，请标注信息来源。"
