# -*- coding: utf-8 -*-
"""
@Time ： 2026/7/9 下午 11:22
@Auth ： Yu
@File ：demo.py
@IDE ：PyCharm
@Intro : 
"""
# Please install OpenAI SDK first: `pip3 install openai`
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
from dotenv import load_dotenv
from openai import OpenAI

# 加载环境变量
load_dotenv()

client = OpenAI(
    api_key=os.environ.get('DEEPSEEK_API_KEY'),
    base_url="https://api.deepseek.com")

response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=[
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "简单介绍一下你自己"},
    ],
    stream=False,
    reasoning_effort="high",
    extra_body={"thinking": {"type": "enabled"}}
)

print(response.choices[0].message.content)


# ===== Embedding 测试 =====
from langchain_huggingface import HuggingFaceEmbeddings

# 初始化 BGE-M3（首次运行会下载模型，约2GB，需要几分钟）
embeddings = HuggingFaceEmbeddings(
    model_name="BAAI/bge-m3",
    model_kwargs={"device": "cpu"},
)

# 测试：把两句话转向量，看维度
vec1 = embeddings.embed_query("网络安全")
vec2 = embeddings.embed_query("信息安全")

print(f"向量维度: {len(vec1)}")
print(f"两个相似词的向量前5位: {vec1[:5]}")
print(f"对比词的向量前5位: {vec2[:5]}")


# ===== ChromaDB 测试 =====
from langchain_community.vectorstores import Chroma

# 用前面的 embeddings 对象
texts = [
    "网络安全是保护系统免受攻击的实践",
    "Python是一种编程语言，适合快速开发",
    "SQL注入是常见的Web漏洞",
]

# 存入 ChromaDB（持久化到本地目录）
vectorstore = Chroma.from_texts(
    texts=texts,
    embedding=embeddings,
    persist_directory="./chroma_db",
)

# 检索测试
results = vectorstore.similarity_search("什么是网络安全", k=2)
print("\n检索结果:")
for i, doc in enumerate(results):
    print(f"  {i+1}. {doc.page_content}")