"""RNA 结构预测平台后端（FastAPI）。

运行：在项目根目录执行
    uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import composer_api, rcsb, rna3d, rna_fold, rna_fm

logging.basicConfig(
    level=os.environ.get("RNA_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("rna.api")


def _check_single_process() -> None:
    """深度任务表、限流窗口与 RCSB 缓存都是进程内状态。

    多进程（--workers > 1）时轮询会打到没有该任务的进程（404），限流也会按进程重复，
    因此在启动阶段直接拒绝，而不是等到运行中才出现诡异行为。
    """
    workers = os.environ.get("WEB_CONCURRENCY") or os.environ.get("UVICORN_WORKERS") or ""
    if workers.isdigit() and int(workers) > 1:
        raise RuntimeError(
            f"检测到多进程配置 workers={workers}：任务表/限流/检索缓存在进程内存中，"
            "请以单进程运行（uvicorn --workers 1）。"
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _check_single_process()
    yield
    cancelled = rna_fm.cancel_all()
    if cancelled:
        logger.info("服务关闭：已取消 %d 个未完成深度任务", cancelled)
    models_cancelled = rna3d.cancel_all()
    if models_cancelled:
        logger.info("服务关闭：已取消 %d 个未完成 3D 建模任务", models_cancelled)


app = FastAPI(title="RNA Structure Prediction API", version="0.1.0", lifespan=lifespan)

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


def _trust_proxy() -> bool:
    """是否信任反向代理传来的客户端 IP。

    默认关闭：服务直接暴露公网时，X-Forwarded-For 可被伪造，开启会让限流形同虚设。
    仅在 Nginx / Caddy / 云负载均衡之后部署时设为 1，并同步配置 FORWARDED_ALLOW_IPS。
    """
    return os.environ.get("RNA_TRUST_PROXY", "0").strip().lower() in ("1", "true", "yes", "on")


def _client_ip(request: Request) -> str:
    if _trust_proxy():
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # 代理链中最左侧为真实客户端
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
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


class Model3DRequest(BaseModel):
    sequence: str
    structure: Optional[str] = Field(
        None, description="可选 dot-bracket；缺省时用 ViennaRNA 预测的结构"
    )


def _examples_path() -> str:
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "data",
        "examples",
        "examples.json",
    )


_PDB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "pdb")


def _local_pdb(pdb_id: Optional[str]) -> Optional[str]:
    """返回本地已收录的 PDB 文件路径（离线 3D 查看用），未收录返回 None。"""
    if not pdb_id or not re.fullmatch(r"[0-9A-Za-z]{4}", pdb_id):
        return None
    path = os.path.join(_PDB_DIR, f"{pdb_id.upper()}.pdb")
    return path if os.path.isfile(path) else None


_EXAMPLES_LOCK = threading.Lock()
_EXAMPLES_CACHE: Dict[str, object] = {"mtime": None, "data": []}


def _load_examples() -> List[dict]:
    """按文件 mtime 缓存：原地更新 examples.json 后无需重启服务。"""
    path = _examples_path()
    mtime = os.path.getmtime(path)
    with _EXAMPLES_LOCK:
        if _EXAMPLES_CACHE["mtime"] == mtime:
            return _EXAMPLES_CACHE["data"]
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        _EXAMPLES_CACHE.update(mtime=mtime, data=data)
        return data


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
        **rna3d.engine_detail(),
    }


@app.get("/api/examples")
def examples():
    data = _load_examples()
    return {"examples": data}


@app.get("/api/pdb/{pdb_id}")
def pdb_file(pdb_id: str):
    """返回本地预置的 PDB 坐标，供离线 3D 查看（Mol* 无需外网）。

    未收录的 PDB 返回 404，前端会回退到 RCSB 的公开下载地址。
    """
    if not re.fullmatch(r"[0-9A-Za-z]{4}", pdb_id):
        raise HTTPException(status_code=400, detail="PDB ID 必须是 4 位字母数字")
    path = _local_pdb(pdb_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"本地未收录 {pdb_id.upper()} 的坐标文件")
    return FileResponse(path, media_type="chemical/x-pdb", filename=f"{pdb_id.upper()}.pdb")


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
                "pdb_local": bool(_local_pdb(ex.get("pdb"))),
                "rfam": ex.get("rfam"),
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
        similar = [{**hit, "local": bool(_local_pdb(hit.get("pdb_id")))} for hit in similar]
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
# 本地 3D 建模（序列 + 二级结构 → 粗粒度 PDB，离线可用）
# --------------------------------------------------------------------------- #
@app.post("/api/3d/predict")
def model3d(req: Model3DRequest):
    """提交本地 3D 建模任务，立即返回 job_id（前端轮询进度）。"""
    try:
        seq = rna_fold.normalize_sequence(req.sequence)
        rna_fold.validate_sequence(seq)
        if len(seq) > rna3d.MAX_3D_LEN:
            raise ValueError(f"本地 3D 建模限长 {rna3d.MAX_3D_LEN} nt（当前 {len(seq)} nt）")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if req.structure:
        structure = req.structure.strip()
        if len(structure) != len(seq):
            raise HTTPException(status_code=400, detail="dot-bracket 长度必须与序列一致")
        if any(ch not in ".()[]{}<>" for ch in structure):
            raise HTTPException(status_code=400, detail="dot-bracket 只能包含 . ( ) [ ] { } < >")
    else:
        try:
            structure = rna_fold.fold(seq)["structure"]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    job_id = rna3d.submit(seq, structure)
    return {
        "job_id": job_id,
        "status": "queued",
        "structure": structure,
        "engine": rna3d.engine_detail()["engine_3d"],
    }


@app.get("/api/3d/jobs/{job_id}")
def model3d_job(job_id: str):
    job = rna3d.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job


@app.delete("/api/3d/jobs/{job_id}")
def model3d_cancel(job_id: str):
    """取消排队中或运行中的建模任务。"""
    job = rna3d.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if not rna3d.cancel_job(job_id):
        raise HTTPException(status_code=409, detail=f"任务已处于 {job['status']} 状态，无法取消")
    return {"job_id": job_id, "status": "cancelled"}


@app.get("/api/3d/models/{model_id}")
def model3d_file(model_id: str):
    """返回缓存的模型坐标，供 Mol* 直接加载。"""
    path = rna3d.model_path(model_id)
    if path is None:
        raise HTTPException(status_code=404, detail="模型不存在或已过期")
    return FileResponse(path, media_type="chemical/x-pdb", filename=f"{model_id[:8]}.pdb")


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