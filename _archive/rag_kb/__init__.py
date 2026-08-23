# -*- coding: utf-8 -*-
"""
@Time ： 2026/7/14 下午 10:01
@Auth ： Yu
@File ：__init__.py.py
@IDE ：PyCharm
@Intro : 
"""
import os

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_OFFLINE"] = "1"  # 离线模式，跳过在线检查，直接用缓存
