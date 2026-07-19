# -*- coding: utf-8 -*-
"""
@Time ： 2026/7/18 下午 7:52
@Auth ： Yu
@File ：vector_store.py
@IDE ：PyCharm
@Intro : 封装 ChromaDB 的存取操作，提供存入文档、检索文档、清空等功能。
"""
import shutil
from pathlib import Path

import chromadb
from langchain_core.documents import Document
from langchain_chroma import Chroma
from rag_kb import config, embeddings


class VectorStore:
    def __init__(self, persist_directory: str = str(config.CHROMA_DIR), collection_name: str = config.COLLECTION_NAME):
        """初始化，参数从 config 读取默认值"""
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        self._chroma = None
        self._client = None

    # 可以将方法当属性一样使用
    @property
    def get_chroma(self) -> Chroma:
        """初始化Chroma"""
        if self._chroma is None:
            # 自己创建 chromadb client（公开 API）
            self._client = chromadb.PersistentClient(path=self.persist_directory)
            print("初始化Chroma实例...")
            self._chroma = Chroma(
                collection_name=self.collection_name,
                embedding_function=embeddings.get_embeddings(),
                persist_directory=self.persist_directory,
                client=self._client
            )
        return self._chroma

    # 直接调用Chroma现有的方法，我进行简单的封装
    def add_documents(self, documents: list[Document]) -> None:
        """存入 Document 列表"""
        self.get_chroma.add_documents(documents)

    def add_texts(self, texts: list[str]) -> None:
        """存入纯文本列表（内部转成 Document）"""
        self.get_chroma.add_texts(texts)

    # 返回k个最相关的Document
    def search(self, query: str, k: int = config.SEARCH_K) -> list[Document]:
        """检索最相关的 top-k 文档块"""
        return self.get_chroma.similarity_search(query, k)

    # 返回k个最相关的Document，还包含他们的分数，其中分数代码向量之间的距离，距离越小分数越小，相关性越强
    def search_with_scores(self, query: str, k: int = config.SEARCH_K) -> list[tuple[Document, float]]:
        """检索并返回相似度分数"""
        return self.get_chroma.similarity_search_with_score(query, k)

    # 因为 LCEL 管道的每个环节都得是 Runnable，retriever 实现了 Runnable 接口，能无缝接入 chain | retriever | format_docs 这样的管道
    # 将Chroma转化为retriever，支持retriever.invoke()调用,k为返回的document数量
    def as_retriever(self, k: int = config.SEARCH_K):
        """转为 LangChain retriever"""
        return self.get_chroma.as_retriever(search_kwargs={"k": k})

    # 删除持久化目录并重置内部状态
    def clear(self) -> None:
        """清空向量库（删除持久化目录）"""
        if self._client is not None:
            self._client.close()  # 显式关闭 SQLite 连接 → 立即释放文件句柄 → 可以删除
            self._client = None
        self._chroma = None

        if Path(self.persist_directory).exists():
            shutil.rmtree(self.persist_directory)


if __name__ == "__main__":
    from rag_kb import document_processor

    print("test")
    vs = VectorStore()
    # vs.clear()
    doc = document_processor.DocumentProcessor()
    pdf_docs = doc.load_and_split(config.DATA_DIR / "sample.pdf")
    vs.add_documents(pdf_docs)
    ans_list = vs.search_with_scores("什么是零信任架构", 2)
    for t in ans_list:
        print(t[0].page_content)
        print(t[0].metadata)
        print(t[1])
    print("=" * 100)
    ret = vs.as_retriever()
    ret_docs = ret.invoke("什么是社会工程学攻击")
    print("as_retriever num: "+str(len(ret_docs)))
    for d in ret_docs:
        print(d.page_content)
        print(d.metadata)
    print("=" * 100)
    vs.clear()
    vs_doc = vs.search("什么是社会工程学攻击", 2)
    print("=" * 100)
    print("检索doc数量为： " + str(len(vs_doc)))
    # vs.clear()
