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