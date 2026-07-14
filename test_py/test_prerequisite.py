# -*- coding: utf-8 -*-
"""
@Time ： 2026/7/14 下午 10:14
@Auth ： Yu
@File ：test_prerequisite.py
@IDE ：PyCharm
@Intro : 
"""


def test_getenv():
    print("\n" + "=" * 40)
    import os
    from dotenv import load_dotenv
    load_dotenv()  # load_dotenv() 默认查找的是当前工作目录（运行 Python 时所在的目录）下的 .env 文件，不是脚本文件所在目录。
    print(os.getenv("TEST_KEY"))
    print(os.getcwd())  # 该目录下的即运行 Python 时所在的目录


def test_get_path():
    print("\n" + "=" * 40)
    from pathlib import Path
    print(Path())  # 空路径，代表当前目录
    print(Path(__file__).parent)  # 当前文件所在目录
    print(Path.home())  # 用户家目录
    print(Path.cwd())  # 当前工作目录
    p = Path.cwd() / "data"  #
    print(p)
    if p.exists():  # 检查路径是否存在,flase,
        print("目录存在")
    else:
        print("目录不存在")
        p.mkdir()  # 创建目录
    BASE_DIR = Path.cwd().parent  # 项目根目录
    DATA_DIR = BASE_DIR / "data"  # 数据目录
    CHROMA_DIR = BASE_DIR / "chroma_db"  # 向量库目录
    print(BASE_DIR)  # 项目根目录
    print(DATA_DIR)
    print(CHROMA_DIR)
    print(BASE_DIR.exists())
    print(DATA_DIR.exists())
    print(CHROMA_DIR.exists())