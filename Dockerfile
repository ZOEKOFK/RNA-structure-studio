# ---- 前端构建（产物由后端统一托管，单容器部署）----
FROM node:18-alpine AS frontend
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 运行时 ----
FROM python:3.11-slim

ARG APP_VERSION=0.1.0
ARG GIT_COMMIT=unknown
LABEL org.opencontainers.image.title="rna-fold" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_COMMIT}" \
      org.opencontainers.image.description="RNA secondary/tertiary structure prediction platform"

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
        pip install --no-cache-dir "torch==2.5.1" --index-url https://download.pytorch.org/whl/cpu && \
        pip install --no-cache-dir -r requirements-deep.txt; \
    else \
        echo "跳过 RNA-FM 依赖（INSTALL_DEEP=0），深度分析使用降级嵌入"; \
    fi

COPY backend ./backend
COPY data ./data
COPY --from=frontend /web/dist ./frontend/dist

# RNA-FM 权重不随镜像分发，运行时挂载到 /app/models/rnafm
RUN mkdir -p /app/models /app/cache \
    && groupadd -r app \
    && useradd -r -g app -u 10001 app \
    && chown -R app:app /app
USER app

ENV PYTHONUNBUFFERED=1 \
    RNA_LOG_LEVEL=INFO \
    RNA_CACHE_DIR=/app/cache

EXPOSE 8000
# 健康检查内置到镜像，直接 docker run 部署也具备健康状态
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

# 任务表、限流窗口与 RCSB 缓存均为进程内状态：只能单进程运行，
# 加 --workers >1 或多副本会导致任务轮询 404、限流按进程重复。
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
