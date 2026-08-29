# kb — 本地优先 Agent 记忆服务（容器化，供 MCP Registry / Glama 验证 / Linux 部署）
#
# 两种运行模式（默认 stdio）：
#   1) stdio（MCP 客户端直接拉起，Glama introspection 零模型加载）：
#        docker build -t kb-memory .
#        docker run --rm -i kb-memory
#   2) 常驻 REST + MCP HTTP（暴露 8000，数据持久化）：
#        docker run --rm -p 8000:8000 -v kb-data:/data kb-memory serve
#
# 说明：
#   - torch 显式装 CPU 版（默认版含 CUDA 依赖近 3GB，CPU 镜像足够且瘦身）
#   - 嵌入模型懒加载（首次 embed 才下载）；HF_HOME 指向 /data 便于 volume 持久化
#   - 国内构建若 pytorch 官方源慢，可换阿里云镜像：
#       pip install torch --index-url https://mirrors.aliyun.com/pytorch-wheels/cpu/
FROM python:3.10-slim

# 构建工具兜底：个别依赖（onnxruntime/grpcio/uvloop 等）在 slim 下若缺 wheel 需编译
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/data/hf-cache \
    KB_DATA_DIR=/data \
    KB_LOG_DIR=/data/logs \
    KB_DEVICE=cpu \
    KB_LLM_MODE=auto

WORKDIR /app

# 1) CPU 版 torch（显式指定，避免 pip 解析拉取 CUDA 默认版）
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# 2) 依赖清单（requirements.txt 与 pyproject.toml 同源，设计文档第 3 节）
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

# 3) 应用源码 + 安装（生成 kb 命令，--no-deps 复用上一步依赖）
COPY kb ./kb
COPY .env.example ./
RUN pip install --no-deps --no-cache-dir -e .

# 默认 stdio 模式：MCP 客户端 / Glama 直接拉起，工具集与 REST 同栈
ENTRYPOINT ["kb", "mcp"]
