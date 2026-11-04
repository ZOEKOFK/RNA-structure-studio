# ---- 前端构建（产物由后端统一托管，单容器部署）----
FROM node:18-alpine AS frontend
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 运行时 ----
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt requirements-optional.txt requirements-deep.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# ViennaRNA Python 绑定：PyPI 提供 manylinux wheel（cp311），无需系统 CLI 或编译器。
# 若某平台无 wheel 则跳过，后端自动降级到纯 Python 引擎。
RUN pip install --no-cache-dir -r requirements-optional.txt \
    || echo "ViennaRNA 绑定安装失败，将使用纯 Python 降级引擎"

# 深度层可选：docker compose build --build-arg INSTALL_DEEP=1 启用真实 RNA-FM
# （torch 体积较大，默认关闭；未启用时深度分析走确定性降级嵌入）
ARG INSTALL_DEEP=0
RUN if [ "$INSTALL_DEEP" = "1" ]; then \
        pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
        pip install --no-cache-dir -r requirements-deep.txt; \
    else \
        echo "跳过 RNA-FM 依赖（INSTALL_DEEP=0），深度分析使用降级嵌入"; \
    fi

COPY backend ./backend
COPY data ./data
COPY --from=frontend /web/dist ./frontend/dist

# RNA-FM 权重不随镜像分发，运行时挂载到 /app/models/rnafm
RUN mkdir -p /app/models

ENV PYTHONUNBUFFERED=1 \
    RNA_LOG_LEVEL=INFO

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
