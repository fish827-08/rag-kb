# -*- coding: utf-8 -*-
"""
@Time ： 2026/7/19 下午 7:37
@Auth ： Yu
@File ：rag_chain.py
@IDE ：PyCharm
@Intro : 把 document_processor、vector_store、DeepSeek LLM 串联起来，实现完整的"导入文档→提问→回答"流程
"""
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_deepseek import ChatDeepSeek

from rag_kb import config, document_processor, vector_store


def format_docs(docs: Document) -> str:
    """"将每个docs的page_content拼接在一起，用\n\n隔开"""
    return "\n\n".join(doc.page_content for doc in docs)


# 构造提示词
prompt = ChatPromptTemplate.from_messages([
    ("system", config.SYSTEM_PROMPT),
    ("human", "{input}"),
])


class RAGChain:
    def __init__(self, chunk_size: int = config.CHUNK_SIZE, chunk_overlap: int = config.CHUNK_OVERLAP,
                 persist_directory: str = str(config.CHROMA_DIR), collection_name: str = config.COLLECTION_NAME,
                 k: int = config.SEARCH_K):
        """初始化，创建 document_processor 和 vector_store 实例"""
        self.document_processor = document_processor.DocumentProcessor(chunk_size, chunk_overlap)
        self.vector_store = vector_store.VectorStore(persist_directory, collection_name)
        self.k = k
        self._retriever = None
        self._llm = None
        self._rag_chain = None

    @property
    def get_retriever(self):
        if self._retriever is None:
            self._retriever = self.vector_store.as_retriever(self.k)
        return self._retriever

    @property
    def get_llm(self):
        """返回deepseek的llm"""
        if self._llm is None:
            # 为空则创建
            self._llm = ChatDeepSeek(
                model=config.DEEPSEEK_MODEL,
                api_key=config.DEEPSEEK_API_KEY
            )
        return self._llm  # 有就直接返回

    @property
    def get_rag_chain(self):
        """得到封装好的rag_chain，采用单例模式"""
        if self._rag_chain is None:
            self._rag_chain = (
                    {"context": self.get_retriever | format_docs, "input": RunnablePassthrough()}
                    | prompt
                    | self.get_llm
                    | StrOutputParser()
            )
        return self._rag_chain

    def add_document(self, file_path: str) -> int:
        """加载并存入一个文档，返回切分后的块数量"""
        return len(self.document_processor.load_and_split(file_path))

    def add_documents(self, file_paths: list[str]) -> int:
        """批量加载并存入多个文档，返回总块数"""
        all_num: int = 0
        for fp in file_paths:
            all_num += len(self.document_processor.load_and_split(fp))  # 遍历file_paths获取每个path加入并累计块速
        return all_num

    def ask(self, question: str) -> str:
        """提问，返回回答字符串"""
        return self.get_rag_chain.invoke(question)

    def ask_with_sources(self, question: str) -> dict:
        """提问，返回 {"answer": str, "sources": list[Document]}"""
        answer_str = self.ask(question)
        sources_list = self.get_retriever.invoke(question)
        return {
            "answer": answer_str,
            "sources": sources_list
        }


if __name__ == "__main__":
    chain = RAGChain()
    file_pa = config.DATA_DIR / "sample.txt"
    chain.add_document(str(file_pa))
    chain.ask("常见的Web漏洞有哪些？")
    chain.ask("python是什么？")
    chain.ask_with_sources("常见的Web漏洞有哪些？")


