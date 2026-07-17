# -*- coding: utf-8 -*-
"""
@Time ： 2026/7/17 下午 10:01
@Auth ： Yu
@File ：embeddings.py
@IDE ：PyCharm
@Intro : 
"""
import os

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"  # 先设环境变量
from langchain_huggingface import HuggingFaceEmbeddings
from rag_kb import config

_embedding = None  # 初始化全局变量为空,单例模式


def get_embeddings() -> HuggingFaceEmbeddings:
    """获取 BGE-M3 embedding 实例（延迟加载，单例）

    第一次调用时加载模型，后续调用返回同一实例。
    """
    global _embedding
    if _embedding is None:
        # 初始化 BGE-M3（首次运行会下载模型，约2GB，需要几分钟）
        _embedding = HuggingFaceEmbeddings(
            model_name=config.EMBEDDING_MODEL,
            model_kwargs={"device": config.EMBEDDING_DEVICE},
        )

    return _embedding


if __name__ == "__main__":
    em = get_embeddings()
    print(em)
    ei = get_embeddings()
    print(ei)
    print(ei is em)
    vec1 = ei.embed_query("网络安全")
    print(vec1)
    print(f"向量维度: {len(vec1)}")
    print(f"两个相似词的向量前5位: {vec1[:5]}")
