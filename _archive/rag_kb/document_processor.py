# -*- coding: utf-8 -*-
"""
@Time ： 2026/7/16 下午 7:59
@Auth ： Yu
@File ：document_processor.py
@IDE ：PyCharm
@Intro : 能加载 PDF/TXT/MD 文件，并切分成文本块
"""
from langchain_core.documents import Document
from pathlib import Path
from langchain_community.document_loaders import TextLoader
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from rag_kb import config


class DocumentProcessor:
    # 类变量：所有实例共享同一个值
    supported_formats = [".pdf", ".txt", ".md"]

    # python 构造方法，变量这里创建，赋值，全部变量放在顶部
    def __init__(self, chunk_size: int = config.CHUNK_SIZE, chunk_overlap: int = config.CHUNK_OVERLAP):
        """初始化切分器参数"""
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def load(self, file_path: str) -> list[Document]:
        """加载单个文件，根据扩展名自动选择 loader

        支持 .pdf / .txt / .md
        不支持的格式抛出 ValueError
        文件不存在抛出 FileNotFoundError
        """
        if not Path(file_path).exists():
            raise FileNotFoundError(f"{file_path}不存在")

        suffix = Path(file_path).suffix.lower()  # 取得文件的后缀名 lower()变小写
        if suffix in self.supported_formats:  # 遍历supported_formats，符合的文件类型/通过 self. 访问类变量
            # 支持的类型返回合适的loader加载的list[Document]
            if suffix == ".pdf":
                # 使用PyPDFLoader
                return PyPDFLoader(file_path).load()
            elif suffix in (".md", ".txt"):
                return TextLoader(file_path, encoding="utf-8").load()
        else:
            # 不支持的格式抛出 ValueError
            # 主动抛出异常
            fmt = "/".join(self.supported_formats)
            raise ValueError(f"不支持的格式: {suffix}，仅支持 {fmt}")  # 用于返回异常

    def split(self, documents: list[Document], chunk_size: int = None, chunk_overlap: int = None) -> list[Document]:
        """切分文档，可覆盖默认的 chunk_size 和 chunk_overlap"""
        # 在传入值时替换默认值
        if chunk_size is None:
            chunk_size = self.chunk_size
        if chunk_overlap is None:
            chunk_overlap = self.chunk_overlap
        # 设置切片大小
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,  # 切片的大小
            chunk_overlap=chunk_overlap,  # 切片重叠处的大小
        )
        return splitter.split_documents(documents)  # 切片 这里没对documents进行判空处理因为如果所传入文件为空的情况下就是返回空切片

    def load_and_split(self, file_path: str) -> list[Document]:
        """便捷方法：加载并切分"""
        return self.split(self.load(file_path))


if __name__ == "__main__":
    file_path1 = config.DATA_DIR / "sample.pdf"
    file_path2 = config.DATA_DIR / "sample.docx"
    file_path3 = config.DATA_DIR / "sample.txt"
    file_path4 = config.DATA_DIR / "xxx.txt"
    file_path5 = "../data/sample.txt"

    processor = DocumentProcessor()
    # print(processor.load(file_path1))  # 可以打印
    # # print(processor.load(file_path2))  # 不支持的格式抛出 ValueError
    # print(processor.load(file_path3))  # 可以打印
    # print(processor.load(file_path4))  # 文件不存在
    # docs = processor.load_and_split(file_path1)
    docs = processor.load_and_split(file_path5)
    for doc in docs:
        print(doc)
        print(doc.metadata)
        print(len(doc.page_content))
    import os
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    from rag_kb import embeddings

    ei = embeddings.get_embeddings()
    vec1 = ei.embed_query("网络安全")
    print(vec1)
