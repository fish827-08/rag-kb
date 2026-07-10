# RAG-KB 阶段0：跑通 Demo 实现计划（Windows）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在本地跑通一个基于 LangChain + DeepSeek + BGE-M3 + ChromaDB 的 RAG demo，能传入 PDF 文件并基于内容问答。

**Architecture:** 单文件脚本。离线流程：PDF → PyPDFLoader 加载 → RecursiveCharacterTextSplitter 切分 → BGE-M3 向量化 → 存入 ChromaDB。在线流程：用户提问 → ChromaDB 检索 top-k → 拼接上下文 → DeepSeek 生成回答。

**Tech Stack:** Python 3.10+、LangChain、langchain-deepseek、chromadb、sentence-transformers（BGE-M3）、pypdf