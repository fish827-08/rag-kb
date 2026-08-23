# -*- coding: utf-8 -*-
"""
@Time ： 2026/7/27 下午 11:09
@Auth ： Yu
@File ：main.py
@IDE ：PyCharm
@Intro : FastAPI 实例 + lifespan（初始化 RAGChain）+ 注册路由
"""
from fastapi import FastAPI

app = FastAPI()


# @app.get("/")
# def hello():
#     return {"hello": "world"}


@app.get("/")
def hello(q: str):
    return {f"hello {q}!"}
