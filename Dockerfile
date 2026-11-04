FROM python:3.11-slim

# ViennaRNA 提供真实能量模型；CPU 版 torch 供 RNA-FM 深度分析（阶段 0 验证后启用）
RUN apt-get update && apt-get install -y --no-install-recommends \
        vienna-rna \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-optional.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# ViennaRNA 绑定为可选：失败时保留 apt 安装的 RNAfold CLI，服务仍可用
RUN pip install --no-cache-dir -r requirements-optional.txt \
    || echo "ViennaRNA 绑定不可用，将使用 apt 提供的 RNAfold CLI"

COPY backend ./backend
COPY data ./data

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]