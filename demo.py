# -*- coding: utf-8 -*-
"""
@Time ： 2026/7/9 下午 11:22
@Auth ： Yu
@File ：demo.py
@IDE ：PyCharm
@Intro : 
"""
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek

# 加载环境变量
load_dotenv()

# 初始化 DeepSeek
llm = ChatDeepSeek(
    model="deepseek-v4-flash",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
)

# 发一条消息测试
response = llm.invoke("你好，请用一句话介绍你自己。")
print(response.content)

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


# ===== 完整 RAG 流程 =====
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

# 1. 加载文档
# loader = TextLoader("./data/sample.txt", encoding="utf-8")
from langchain_community.document_loaders import PyPDFLoader
loader = PyPDFLoader("./data/sample.pdf")
docs = loader.load()

# 2. 切分文档
splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=100,
)
chunks = splitter.split_documents(docs)
print(f"\n文档切分: {len(chunks)} 个块")

# 3. 向量化并存入 ChromaDB
vectorstore = Chroma.from_documents(
    documents=chunks,
    embedding=embeddings,
    persist_directory="./chroma_db",
)

# 4. 构建检索器
retriever = vectorstore.as_retriever(search_kwargs={"k": 2})

# 5. 构建 RAG 链
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

# 定义提示词模板
system_prompt = (
    "你是一个文档问答助手。请根据以下检索到的文档内容回答用户问题。"
    "如果文档中没有相关信息，请说'文档中没有相关信息'。"
    "\n\n文档内容:\n{context}"
)

prompt = ChatPromptTemplate.from_messages([
    ("system", system_prompt),
    ("human", "{input}"),
])

def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)

# 组合链
rag_chain = (
    {"context": retriever | format_docs, "input": RunnablePassthrough()}
    | prompt
    | llm
    | StrOutputParser()
)

# 6. 提问
# question = "常见的Web漏洞有哪些？"
# response = rag_chain.invoke(question)
#
# print(f"\n问题: {question}")
# print(f"回答: {response}")
#
# # 再问一个
# question2 = "渗透测试是什么？"
# response2 = rag_chain.invoke(question2)
# print(f"\n问题: {question2}")
# print(f"回答: {response2}")
#
# question3 = "Java的Spring框架怎么用？"
# response3 = rag_chain.invoke(question3)
# print(f"\n问题: {question3}")
# print(f"回答: {response3}")

# 6. 提问
question = "零信任架构的核心原则是什么？"
response = rag_chain.invoke(question)

print(f"\n问题: {question}")
print(f"回答: {response}")

# 再问一个
question2 = "勒索软件的常见攻击手法有哪些？"
response2 = rag_chain.invoke(question2)
print(f"\n问题: {question2}")
print(f"回答: {response2}")

question3 = "区块链技术在网络安全中有什么应用？"
response3 = rag_chain.invoke(question3)
print(f"\n问题: {question3}")
print(f"回答: {response3}")