"""RNA 结构预测平台后端（FastAPI）。

运行：在项目根目录执行
    uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import json
import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import rna_fold, rna_fm

app = FastAPI(title="RNA Structure Prediction API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_RNACOMPOSER_MAX_LEN = 300


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
def tertiary(req: TertiaryRequest):
    try:
        seq = rna_fold.normalize_sequence(req.sequence)
        rna_fold.validate_sequence(seq)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    for ex in _load_examples():
        if rna_fold.normalize_sequence(ex.get("sequence", "")) == seq:
            return {
                "matched": True,
                "source": "prebuilt",
                "name": ex.get("name"),
                "pdb": ex.get("pdb"),
                "annotation": ex.get("annotation"),
                "message": "命中预置结构库，直接展示已知 3D 结构。",
            }

    if len(seq) <= _RNACOMPOSER_MAX_LEN:
        message = (
            "未命中预置结构库。接入 RNAComposer API（backend/composer_api.py）"
            "后即可在线预测；当前先展示预置示例分子。"
        )
    else:
        message = f"未命中预置结构库，且长度 {len(seq)} nt 超过 3D 预测限长 {_RNACOMPOSER_MAX_LEN} nt。"

    return {"matched": False, "source": None, "pdb": None, "message": message}


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