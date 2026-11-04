"""RNA 结构预测平台后端（FastAPI）。

运行：在项目根目录执行
    uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from functools import lru_cache
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import composer_api, rcsb, rna_fold, rna_fm

logging.basicConfig(
    level=os.environ.get("RNA_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("rna.api")

app = FastAPI(title="RNA Structure Prediction API", version="0.1.0")

# 默认放开全部来源方便本地前端；生产可设 RNA_CORS_ORIGINS=http://a.com,http://b.com
_origins = [o.strip() for o in os.environ.get("RNA_CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

_RNACOMPOSER_MAX_LEN = composer_api.RNACOMPOSER_MAX_LEN   # RNAComposer 官方交互模式限长（500 nt）


def _env_int(name: str, default: int) -> int:
    """读取整型环境变量，非法值回退默认值（<=0 表示关闭）。"""
    try:
        value = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return value


# /api/tertiary 会向外请求 RCSB，按客户端 IP 做简单滑动窗口限流，防止被放大成外部流量。
_RATE_LIMIT = _env_int("RNA_TERTIARY_RATE_LIMIT", 30)    # 每 IP 每分钟允许次数；0=关闭
_RATE_WINDOW = 60.0
_RATE_LOCK = threading.Lock()
_RATE_HITS: Dict[str, List[float]] = {}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limit(request: Request) -> None:
    if _RATE_LIMIT <= 0:
        return
    ip = _client_ip(request)
    now = time.time()
    with _RATE_LOCK:
        hits = [t for t in _RATE_HITS.get(ip, []) if now - t < _RATE_WINDOW]
        if len(hits) >= _RATE_LIMIT:
            retry_after = int(_RATE_WINDOW - (now - hits[0])) + 1
            raise HTTPException(
                status_code=429,
                detail=f"请求过于频繁，请 {retry_after} 秒后重试",
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)
        _RATE_HITS[ip] = hits
        if len(_RATE_HITS) > 1024:
            # 清理窗口内已无请求的 IP，避免字典无限增长
            for key in [k for k, v in _RATE_HITS.items() if not v or now - v[-1] >= _RATE_WINDOW]:
                _RATE_HITS.pop(key, None)


class FoldRequest(BaseModel):
    sequence: str


class PairProbRequest(BaseModel):
    sequence: str


class MutateRequest(BaseModel):
    sequence: str
    position: int = Field(..., ge=1, description="1-based 突变位点")
    base: str


class TertiaryRequest(BaseModel):
    sequence: str


class DeepEmbedRequest(BaseModel):
    sequence: str


class DeepMutateRequest(BaseModel):
    sequence: str
    position: int = Field(..., ge=1, description="1-based 突变位点")
    base: str


def _examples_path() -> str:
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "data",
        "examples",
        "examples.json",
    )


@lru_cache(maxsize=1)
def _load_examples() -> List[dict]:
    with open(_examples_path(), encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/health")
def health():
    engine = rna_fold.engine_status()
    vienna = engine.startswith("viennarna")
    return {
        "status": "ok",
        "engine": engine,
        "rnafold_available": vienna,
        "viennarna_version": rna_fold.engine_version(),
        "max_sequence_len": rna_fold.MAX_SEQUENCE_LEN,
        "pairprob_max_len": rna_fold.BPP_MAX_LEN if rna_fold._HAS_RNA else rna_fold.BOLTZMANN_MAX_LEN,
        **rna_fm.engine_detail(),
    }


@app.get("/api/examples")
def examples():
    data = _load_examples()
    return {"examples": data}


@app.post("/api/fold")
def fold(req: FoldRequest):
    try:
        return rna_fold.fold(req.sequence)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/pairprob")
def pairprob(req: PairProbRequest):
    try:
        return rna_fold.pair_probability(req.sequence)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/mutate")
def mutate(req: MutateRequest):
    try:
        seq = rna_fold.normalize_sequence(req.sequence)
        rna_fold.validate_sequence(seq)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    base = req.base.upper().replace("T", "U")
    if base not in "AUCG":
        raise HTTPException(status_code=400, detail="碱基必须是 A/U/C/G")
    if not (1 <= req.position <= len(seq)):
        raise HTTPException(status_code=400, detail="突变位点超出序列范围")

    mutated = seq[: req.position - 1] + base + seq[req.position :]
    try:
        wt = rna_fold.fold(seq)
        mt = rna_fold.fold(mutated)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    wt_pairs = {tuple(p) for p in wt["pairs"]}
    mt_pairs = {tuple(p) for p in mt["pairs"]}
    broken = sorted([list(p) for p in wt_pairs - mt_pairs])
    formed = sorted([list(p) for p in mt_pairs - wt_pairs])
    delta = round((mt["mfe"] or 0.0) - (wt["mfe"] or 0.0), 2)

    note = None
    if wt.get("engine") == "fallback":
        note = "ΔMFE 由降级引擎估计，数值仅供参考"

    return {
        "sequence": seq,
        "mutated": mutated,
        "position": req.position,
        "base": base,
        "wildtype": wt,
        "mutant": mt,
        "delta_mfe": delta,
        "broken_pairs": broken,
        "formed_pairs": formed,
        "note": note,
    }


@app.post("/api/tertiary")
def tertiary(req: TertiaryRequest, request: Request):
    _check_rate_limit(request)
    try:
        seq = rna_fold.normalize_sequence(req.sequence)
        rna_fold.validate_sequence(seq)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # dot-bracket 用于配套展示与 RNAComposer 输入；结构预测失败不阻断 3D 查询
    try:
        structure = rna_fold.fold(seq)["structure"] if len(seq) <= _RNACOMPOSER_MAX_LEN else ""
    except Exception:
        structure = ""

    bridge = {
        "url": composer_api.RNACOMPOSER_URL,
        "input": composer_api.build_input(seq, structure or None),
    }

    for ex in _load_examples():
        if rna_fold.normalize_sequence(ex.get("sequence", "")) == seq:
            return {
                "matched": True,
                "source": "prebuilt",
                "name": ex.get("name"),
                "pdb": ex.get("pdb"),
                "annotation": ex.get("annotation"),
                "sequence": seq,
                "structure": structure,
                "similar": [],
                "rcsb_error": None,
                "rnacomposer": bridge,
                "message": "命中预置结构库，直接展示已知 3D 结构。",
            }

    # 未命中预置库：用 RCSB PDB 公开 Search API 找相似的实验结构（无需 key）
    similar: List[dict] = []
    rcsb_error: Optional[str] = None
    if 10 <= len(seq) <= _RNACOMPOSER_MAX_LEN:
        try:
            similar = rcsb.search_similar(seq)
        except Exception as e:  # 网络不可达 / 服务异常时降级为手动桥接
            rcsb_error = f"{type(e).__name__}: {e}"
            logger.warning("RCSB 序列检索失败: %s", rcsb_error)

    if similar:
        source = "rcsb"
        message = (
            f"未命中预置结构库；RCSB PDB 找到 {len(similar)} 条相似实验结构"
            f"（序列相似度 ≥90%）。"
        )
    elif len(seq) > _RNACOMPOSER_MAX_LEN:
        source = None
        message = (
            f"未命中预置结构库，且长度 {len(seq)} nt 超过 3D 检索/预测限长 "
            f"{_RNACOMPOSER_MAX_LEN} nt。"
        )
    elif rcsb_error:
        source = None
        message = "未命中预置结构库；RCSB 检索暂不可用，可复制下方输入到 RNAComposer 手动预测。"
    else:
        source = None
        message = "未命中预置结构库，RCSB 中也没有 ≥90% 相似的实验结构；可用 RNAComposer 手动预测。"

    return {
        "matched": False,
        "source": source,
        "pdb": similar[0]["pdb_id"] if similar else None,
        "sequence": seq,
        "structure": structure,
        "similar": similar,
        "rcsb_error": rcsb_error,
        "rnacomposer": bridge,
        "message": message,
    }


@app.post("/api/deep/embed")
def deep_embed(req: DeepEmbedRequest):
    """提交逐位置嵌入任务，立即返回 job_id（前端轮询进度）。"""
    try:
        seq = rna_fold.normalize_sequence(req.sequence)
        rna_fold.validate_sequence(seq)
        if len(seq) > rna_fm.MAX_DEEP_LEN:
            raise ValueError(f"深度分析限长 {rna_fm.MAX_DEEP_LEN} nt（当前 {len(seq)} nt）")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    job_id = rna_fm.submit("embed", rna_fm.embed, req.sequence)
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/deep/mutate")
def deep_mutate(req: DeepMutateRequest):
    """提交突变嵌入偏移任务，立即返回 job_id。"""
    try:
        seq = rna_fold.normalize_sequence(req.sequence)
        rna_fold.validate_sequence(seq)
        if len(seq) > rna_fm.MAX_DEEP_LEN:
            raise ValueError(f"深度分析限长 {rna_fm.MAX_DEEP_LEN} nt（当前 {len(seq)} nt）")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    base = req.base.upper().replace("T", "U")
    if base not in "AUCG":
        raise HTTPException(status_code=400, detail="碱基必须是 A/U/C/G")
    if not (1 <= req.position <= len(seq)):
        raise HTTPException(status_code=400, detail="突变位点超出序列范围")

    job_id = rna_fm.submit("deep_mutate", rna_fm.mutate_shift, req.sequence, req.position, base)
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/deep/jobs/{job_id}")
def deep_job(job_id: str):
    job = rna_fm.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job


@app.delete("/api/deep/jobs/{job_id}")
def deep_cancel(job_id: str):
    """取消排队中或运行中的深度任务。"""
    job = rna_fm.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if not rna_fm.cancel_job(job_id):
        raise HTTPException(status_code=409, detail=f"任务已处于 {job['status']} 状态，无法取消")
    logger.info("深度任务已取消 job_id=%s", job_id)
    return {"job_id": job_id, "status": "cancelled"}


# --------------------------------------------------------------------------- #
# 静态前端（单容器部署）
# 本地开发用 Vite（npm run dev），此处仅在存在 frontend/dist 时挂载，
# 让 Docker 镜像 / 生产环境一个进程同时提供页面与 API。
# 必须放在所有 /api 路由之后注册，否则会抢先匹配。
# --------------------------------------------------------------------------- #
_DIST_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist"
)

if os.path.isdir(_DIST_DIR):
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    _ASSETS_DIR = os.path.join(_DIST_DIR, "assets")
    if os.path.isdir(_ASSETS_DIR):
        app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="接口不存在")
        root = os.path.realpath(_DIST_DIR)
        candidate = os.path.realpath(os.path.join(root, full_path))
        # 防止 ../ 越界读取 dist 之外的任意文件
        if full_path and candidate.startswith(root + os.sep) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(root, "index.html"))