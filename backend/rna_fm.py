"""RNA-FM 深度层：逐位置嵌入 + 突变嵌入偏移。

分层策略（与构建方案一致）：
1. **真实 RNA-FM**（``torch`` + ``transformers``，640 维）——若依赖与权重可用。
   权重优先从本地 ``models/rnafm/`` 加载，否则走 ``hf-mirror.com`` 的
   ``multimolecule/rnafm``。
2. **确定性降级嵌入**（纯 Python，34 维）——无 torch 时使用，把碱基组成、
   局部三核苷酸、配对状态与二级结构上下文编码成向量，能量为 0 依赖。

无论哪一级，均在后台线程计算、结果按序列哈希落盘缓存，避免阻塞主流程。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from . import rna_fold


def _env_int(name: str, default: int) -> int:
    """读取整型环境变量，非法值回退默认值。"""
    try:
        value = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


MAX_DEEP_LEN = _env_int("RNA_MAX_DEEP_LEN", 300)          # 深度分析限长（CPU 推理延迟控制）
_CACHE_DIR = os.environ.get("RNA_CACHE_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "cache", "embeddings"
)
_CACHE_MAX_FILES = _env_int("RNA_CACHE_MAX_FILES", 500)   # 缓存文件上限，超出按最近使用时间淘汰
_LOCAL_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models", "rnafm")
_HF_REPO = "multimolecule/rnafm"
_MODEL_NAME = "RNA-FM (multimolecule/rnafm)"

_CACHE_LOCK = threading.Lock()
_MODEL_LOCK = threading.Lock()
_MODEL = None
_TOKENIZER = None
_MODEL_TRIED = False
_MODEL_ERROR: Optional[str] = None
_MODEL_STAGE = ""      # 当前权重加载/下载阶段，供前端展示“正在准备模型”

_EXECUTOR = ThreadPoolExecutor(max_workers=1)
_JOB_LOCK = threading.Lock()
_JOBS: Dict[str, dict] = {}
_JOB_EVENTS: Dict[str, threading.Event] = {}
_JOB_ORDER: List[str] = []
_MAX_JOBS = 50


class JobCancelled(Exception):
    """协作式取消信号：在计算阶段边界抛出，尽快释放后台 worker。"""


# 后台线程的取消事件（ThreadPoolExecutor 只有一个 worker，用 thread-local 传递）
_CURRENT_CANCEL = threading.local()


def _raise_if_cancelled() -> None:
    event = getattr(_CURRENT_CANCEL, "event", None)
    if event is not None and event.is_set():
        raise JobCancelled()


# --------------------------------------------------------------------------- #
# 引擎状态
# --------------------------------------------------------------------------- #
def engine_status() -> str:
    """返回当前会使用的深度引擎名（不触发权重加载）。"""
    if _MODEL is not None:
        return "rna-fm"
    if _MODEL_TRIED:
        # 已尝试加载但失败（缺权重 / 网络错误），保持降级，避免反复重试
        return "fallback"
    has_torch = importlib.util.find_spec("torch") is not None
    has_lib = (importlib.util.find_spec("multimolecule") is not None
               or importlib.util.find_spec("transformers") is not None)
    if has_torch and has_lib:
        return "rna-fm"
    return "fallback"


def engine_detail() -> dict:
    return {
        "deep_engine": engine_status(),
        "deep_model": _MODEL_NAME,
        "deep_max_len": MAX_DEEP_LEN,
        "deep_ready": _MODEL is not None,
        "deep_error": _MODEL_ERROR,
        "deep_cache_max": _CACHE_MAX_FILES,
        "deep_note": _model_load_note(),
    }


def _model_load_note() -> Optional[str]:
    """未加载权重时提示首次准备代价（本地加载或在线下载）。"""
    if _MODEL is not None or engine_status() == "fallback":
        return None
    if _has_local_weights(_LOCAL_MODEL_DIR):
        return "首次深度分析将加载本地 RNA-FM 权重（约 398 MB）"
    return "首次深度分析将从 hf-mirror 下载 RNA-FM 权重（约 398 MB），耗时取决于网络"


def _checkpoint_path(src: str) -> str:
    """定位权重文件：本地目录优先，其次 hf-mirror 仓库。"""
    if os.path.isdir(src):
        for name in ("pytorch_model.bin", "model.safetensors"):
            path = os.path.join(src, name)
            if os.path.isfile(path):
                return path
        raise RuntimeError(f"{src} 中未找到 RNA-FM 权重（pytorch_model.bin / model.safetensors）")
    from huggingface_hub import hf_hub_download

    return hf_hub_download(repo_id=src, filename="pytorch_model.bin")


def _load_backbone_weights(model, src: str) -> None:
    """把预训练检查点装入 RnaFmModel 骨干。

    RNA-FM 的检查点是“骨干 + 预测头”格式（键名以 ``model.`` 开头，
    另有 ``lm_head.*`` / ``ss_head.*``），与 ``RnaFmModel`` 的参数名不一致，
    直接 ``from_pretrained`` 会静默地全部随机初始化，因此这里手工剥离前缀。
    """
    import torch

    path = _checkpoint_path(src)
    if path.endswith(".safetensors"):
        from safetensors.torch import load_file

        state = load_file(path)
    else:
        state = torch.load(path, map_location="cpu", weights_only=True)

    backbone = {k[len("model."):]: v for k, v in state.items() if k.startswith("model.")} or state
    result = model.load_state_dict(backbone, strict=False)
    if result.unexpected_keys:
        raise RuntimeError(f"权重键不匹配：{result.unexpected_keys[:3]}")


def _has_local_weights(path: str) -> bool:
    """本地目录里是否真的有权重文件（空目录不应拦截在线下载）。"""
    return any(
        os.path.isfile(os.path.join(path, name))
        for name in ("pytorch_model.bin", "model.safetensors")
    )


def _load_model():
    """惰性加载真实 RNA-FM；失败则返回 (None, None) 并记录原因。"""
    global _MODEL, _TOKENIZER, _MODEL_TRIED, _MODEL_ERROR, _MODEL_STAGE
    if _MODEL_TRIED:
        return _MODEL, _TOKENIZER
    with _MODEL_LOCK:
        if _MODEL_TRIED:
            return _MODEL, _TOKENIZER
        _MODEL_TRIED = True
        try:
            import torch  # noqa: F401

            os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
            src = _LOCAL_MODEL_DIR if _has_local_weights(_LOCAL_MODEL_DIR) else _HF_REPO
            _MODEL_STAGE = (
                "正在加载本地 RNA-FM 权重（约 398 MB）…"
                if src == _LOCAL_MODEL_DIR
                else "正在下载 RNA-FM 权重（约 398 MB，首次较慢）…"
            )
            try:
                # 官方推荐方式：multimolecule 包注册 rnafm 架构
                import multimolecule  # noqa: F401
                from multimolecule import RnaFmConfig, RnaFmModel, RnaTokenizer

                try:
                    tok = RnaTokenizer.from_pretrained(src)
                except Exception:
                    # multimolecule 0.0.8 把 extra_special_tokens 传成 list，
                    # 与新版 transformers 不兼容；RNA-FM 词表固定，用内置词表即可
                    tok = RnaTokenizer()
                model = RnaFmModel(RnaFmConfig.from_pretrained(src))
                _load_backbone_weights(model, src)
            except ImportError:
                # 兼容带自定义建模代码的仓库
                from transformers import AutoModel, AutoTokenizer

                tok = AutoTokenizer.from_pretrained(src, trust_remote_code=True)
                model = AutoModel.from_pretrained(src, trust_remote_code=True)
            model.eval()
            _TOKENIZER, _MODEL = tok, model
            _MODEL_STAGE = ""
        except Exception as e:  # pragma: no cover - 环境相关
            _MODEL_ERROR = f"{type(e).__name__}: {e}"
            _MODEL, _TOKENIZER = None, None
            _MODEL_STAGE = ""
    return _MODEL, _TOKENIZER


# --------------------------------------------------------------------------- #
# 嵌入计算
# --------------------------------------------------------------------------- #
def _real_vectors(seq: str) -> List[List[float]]:
    model, tok = _load_model()
    if model is None:
        raise RuntimeError(_MODEL_ERROR or "RNA-FM 不可用")

    import torch

    inputs = tok(seq, return_tensors="pt")
    with torch.no_grad():
        out = model(**inputs)
    hidden = out.last_hidden_state[0].tolist()

    n = len(seq)
    if len(hidden) == n + 2:
        # 去掉首尾 <cls> / <eos>，只保留逐碱基位置
        hidden = hidden[1:-1]
    elif len(hidden) > n:
        hidden = hidden[:n]
    return hidden


def _one_hot(base: str, symbols: str = "AUCG") -> List[float]:
    return [1.0 if base == s else 0.0 for s in symbols]


def _fallback_vectors(seq: str, structure: str) -> List[List[float]]:
    """34 维确定性特征：碱基 / 邻位 / 位置编码 / 三核苷酸 / 二级结构上下文。"""
    n = len(seq)
    pairs = rna_fold.dot_bracket_to_pairs(structure)
    partner = {}
    for i, j in pairs:
        partner[i], partner[j] = j, i

    # 每个位置的嵌套深度（栈深度），用于区分茎区与环区
    depth = [0] * n
    stack = 0
    for i, ch in enumerate(structure):
        if ch == "(":
            depth[i] = stack
            stack += 1
        elif ch == ")":
            stack = max(0, stack - 1)
            depth[i] = stack
        else:
            depth[i] = stack

    vectors: List[List[float]] = []
    for i in range(n):
        _raise_if_cancelled()
        b = seq[i]
        prev = seq[i - 1] if i > 0 else "A"
        nxt = seq[i + 1] if i + 1 < n else "A"

        vec: List[float] = []
        vec += _one_hot(b)                              # 4 当前碱基
        vec += _one_hot(prev)                           # 4 前一位
        vec += _one_hot(nxt)                            # 4 后一位

        for k in (1.0, 2.0, 4.0, 8.0):                  # 8 位置编码
            vec.append(math.sin(math.pi * k * i / max(n, 1)))
            vec.append(math.cos(math.pi * k * i / max(n, 1)))

        tri = seq[max(0, i - 1): i + 2].ljust(3, "A")[:3]   # 8 三核苷酸哈希桶
        bucket = int(hashlib.md5(tri.encode()).hexdigest(), 16) % 8
        vec += [1.0 if d == bucket else 0.0 for d in range(8)]

        vec.append(1.0 if i in partner else 0.0)        # 1 是否配对
        vec.append((abs(partner[i] - i) / n) if i in partner else 0.0)  # 1 配对距离

        pair_type = "none"                              # 3 配对类型
        if i in partner:
            p = seq[partner[i]]
            if {b, p} == {"G", "C"}:
                pair_type = "GC"
            elif {b, p} == {"A", "U"}:
                pair_type = "AU"
            elif {b, p} == {"G", "U"}:
                pair_type = "GU"
        vec += [1.0 if pair_type == t else 0.0 for t in ("GC", "AU", "GU")]

        vec.append(depth[i] / max(1, max(depth)))       # 1 结构嵌套深度
        vectors.append(vec)
    return vectors


def _compute_vectors(seq: str) -> Tuple[List[List[float]], str, str, Optional[str]]:
    """返回 (向量, engine, structure, 降级原因)。"""
    structure = rna_fold.fold(seq)["structure"]
    try:
        vectors = _real_vectors(seq)
    except JobCancelled:
        raise
    except Exception as e:
        reason = _MODEL_ERROR or f"{type(e).__name__}: {e}"
        return _fallback_vectors(seq, structure), "fallback", structure, reason
    # torch 前向不可中断，返回后再确认一次，避免取消后仍继续做 PCA
    _raise_if_cancelled()
    return vectors, "rna-fm", structure, None


# --------------------------------------------------------------------------- #
# 缓存
# --------------------------------------------------------------------------- #
def _cache_path(seq: str, engine: str) -> str:
    key = hashlib.sha1(f"{engine}|{MAX_DEEP_LEN}|{seq}".encode()).hexdigest()
    return os.path.join(_CACHE_DIR, f"{key}.json")


def _read_cache(seq: str, engine: str) -> Optional[dict]:
    path = _cache_path(seq, engine)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("sequence") == seq and data.get("engine") == engine:
            # 刷新访问时间，供 LRU 淘汰判断“最近使用”而非“最近写入”
            os.utime(path, None)
            return data
    except Exception:
        pass
    return None


def _prune_cache() -> None:
    """缓存文件数超过上限时，删除最久未被读取的文件。"""
    with _CACHE_LOCK:
        try:
            entries = []
            with os.scandir(_CACHE_DIR) as it:
                for entry in it:
                    if entry.is_file() and entry.name.endswith(".json"):
                        try:
                            entries.append((entry.stat().st_mtime, entry.path))
                        except OSError:
                            pass
            excess = len(entries) - _CACHE_MAX_FILES
            if excess <= 0:
                return
            entries.sort()
            for _, path in entries[:excess]:
                try:
                    os.remove(path)
                except OSError:
                    pass
        except OSError:
            pass


def _write_cache(seq: str, engine: str, structure: str, vectors: List[List[float]]) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    path = _cache_path(seq, engine)
    payload = {
        "sequence": seq,
        "engine": engine,
        "structure": structure,
        "dim": len(vectors[0]) if vectors else 0,
        "vectors": [[round(float(x), 3) for x in v] for v in vectors],
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass
    _prune_cache()


def _cached_vectors(seq: str, engine: str) -> Tuple[Optional[dict], str, Optional[str]]:
    cached = _read_cache(seq, engine)
    if cached:
        return cached, cached.get("structure", ""), "cache"
    vectors, used_engine, structure, reason = _compute_vectors(seq)
    if used_engine != engine:
        # 预期引擎加载失败而降级：按实际引擎回读一次，避免历史降级缓存永远不命中
        cached = _read_cache(seq, used_engine)
        if cached:
            return cached, cached.get("structure", ""), "cache"
    _write_cache(seq, used_engine, structure, vectors)
    return {"sequence": seq, "engine": used_engine, "structure": structure,
            "dim": len(vectors[0]) if vectors else 0, "vectors": vectors}, structure, "compute"


# --------------------------------------------------------------------------- #
# PCA（纯 Python 幂迭代，避免引入 numpy）
# --------------------------------------------------------------------------- #
def _pca2(vectors: List[List[float]]) -> List[List[float]]:
    n = len(vectors)
    if n == 0:
        return []
    d = len(vectors[0])
    if n < 3 or d == 0:
        return [[0.0, 0.0] for _ in range(n)]

    mean = [sum(v[k] for v in vectors) / n for k in range(d)]
    centered = [[v[k] - mean[k] for k in range(d)] for v in vectors]

    def power_iter(data: List[List[float]], iters: int = 40) -> List[float]:
        vec = [1.0 / math.sqrt(d)] * d
        for _ in range(iters):
            _raise_if_cancelled()
            proj = [sum(row[k] * vec[k] for k in range(d)) for row in data]
            new = [sum(row[k] * p for row, p in zip(data, proj)) for k in range(d)]
            norm = math.sqrt(sum(x * x for x in new)) or 1.0
            vec = [x / norm for x in new]
        return vec

    comp1 = power_iter(centered)
    proj1 = [sum(row[k] * comp1[k] for k in range(d)) for row in centered]
    deflated = [[row[k] - p * comp1[k] for k in range(d)] for row, p in zip(centered, proj1)]
    comp2 = power_iter(deflated)
    proj2 = [sum(row[k] * comp2[k] for k in range(d)) for row in centered]

    scale = max(max(abs(x) for x in proj1) or 1.0, max(abs(x) for x in proj2) or 1.0)
    return [[round(proj1[i] / scale, 3), round(proj2[i] / scale, 3)] for i in range(n)]


def _project(vectors: List[List[float]], comps: List[List[float]], mean: List[float],
             scale: float) -> List[List[float]]:
    d = len(mean)
    out = []
    for row in vectors:
        c = [row[k] - mean[k] for k in range(d)]
        seq2 = [sum(c[k] * comp[k] for k in range(d)) / scale for comp in comps]
        out.append([round(seq2[0], 3), round(seq2[1], 3)])
    return out


def _pca_components(vectors: List[List[float]]):
    """返回 (components, mean, scale)，供突变前后共享投影空间。"""
    n, d = len(vectors), len(vectors[0])
    mean = [sum(v[k] for v in vectors) / n for k in range(d)]
    centered = [[v[k] - mean[k] for k in range(d)] for v in vectors]

    def power_iter(data):
        vec = [1.0 / math.sqrt(d)] * d
        for _ in range(40):
            _raise_if_cancelled()
            proj = [sum(row[k] * vec[k] for k in range(d)) for row in data]
            new = [sum(row[k] * p for row, p in zip(data, proj)) for k in range(d)]
            norm = math.sqrt(sum(x * x for x in new)) or 1.0
            vec = [x / norm for x in new]
        return vec

    comp1 = power_iter(centered)
    proj1 = [sum(row[k] * comp1[k] for k in range(d)) for row in centered]
    deflated = [[row[k] - p * comp1[k] for k in range(d)] for row, p in zip(centered, proj1)]
    comp2 = power_iter(deflated)
    scale = max(max(abs(x) for x in proj1) or 1.0, 1e-6)
    return [comp1, comp2], mean, scale


# --------------------------------------------------------------------------- #
# 对外计算（供后台任务调用）
# --------------------------------------------------------------------------- #
def embed(sequence: str, progress=None) -> dict:
    seq = rna_fold.normalize_sequence(sequence)
    rna_fold.validate_sequence(seq)
    if len(seq) > MAX_DEEP_LEN:
        raise ValueError(f"深度分析限长 {MAX_DEEP_LEN} nt（当前 {len(seq)} nt）")

    if progress:
        progress(0.15)
    _raise_if_cancelled()
    data, structure, source = _cached_vectors(seq, engine_status())
    vectors = data["vectors"]
    if progress:
        progress(0.7)
    _raise_if_cancelled()

    norms = [round(math.sqrt(sum(x * x for x in v)), 3) for v in vectors]
    pca2 = _pca2(vectors)
    if progress:
        progress(1.0)

    return {
        "engine": data["engine"],
        "model": _MODEL_NAME if data["engine"] == "rna-fm" else "序列+结构特征（降级嵌入）",
        "dim": data.get("dim", 0),
        "sequence": seq,
        "structure": structure,
        "length": len(seq),
        "pca2": pca2,
        "norm": norms,
        "source": source,
        "fallback_reason": None if data["engine"] == "rna-fm" else (_MODEL_ERROR or "torch/transformers 未安装，使用确定性特征嵌入"),
    }


def mutate_shift(sequence: str, position: int, base: str, progress=None) -> dict:
    seq = rna_fold.normalize_sequence(sequence)
    rna_fold.validate_sequence(seq)
    if len(seq) > MAX_DEEP_LEN:
        raise ValueError(f"深度分析限长 {MAX_DEEP_LEN} nt（当前 {len(seq)} nt）")
    if not (1 <= position <= len(seq)):
        raise ValueError("突变位点超出序列范围")
    base = base.upper().replace("T", "U")
    if base not in "AUCG":
        raise ValueError("碱基必须是 A/U/C/G")

    mutated = seq[: position - 1] + base + seq[position:]
    if progress:
        progress(0.1)

    wt, wt_struct, _ = _cached_vectors(seq, engine_status())
    if progress:
        progress(0.55)
    _raise_if_cancelled()
    mt, mt_struct, _ = _cached_vectors(mutated, engine_status())
    if progress:
        progress(0.8)
    _raise_if_cancelled()

    # 两侧维度可能不同（模型可用性变化），统一用较短维度比较
    dim = min(wt.get("dim", 0), mt.get("dim", 0))
    shift = []
    for a, b in zip(wt["vectors"], mt["vectors"]):
        _raise_if_cancelled()
        shift.append(round(math.sqrt(sum((a[k] - b[k]) ** 2 for k in range(dim))), 3))

    mean_shift = round(sum(shift) / len(shift), 3) if shift else 0.0
    max_pos = (max(range(len(shift)), key=lambda i: shift[i]) + 1) if shift else 0
    max_val = max(shift) if shift else 0.0

    combined = wt["vectors"] + mt["vectors"]
    comps, mean_vec, scale = _pca_components(combined)
    pca_wt = _project(wt["vectors"], comps, mean_vec, scale)
    pca_mt = _project(mt["vectors"], comps, mean_vec, scale)

    top = sorted(range(len(shift)), key=lambda i: shift[i], reverse=True)[:5]
    if progress:
        progress(1.0)

    return {
        "engine": wt["engine"],
        "model": _MODEL_NAME if wt["engine"] == "rna-fm" else "序列+结构特征（降级嵌入）",
        "dim": dim,
        "sequence": seq,
        "mutated": mutated,
        "position": position,
        "base": base,
        "length": len(seq),
        "shift": shift,
        "mean_shift": mean_shift,
        "max_shift": max_val,
        "max_shift_position": max_pos,
        "top_positions": [i + 1 for i in top],
        "structure_wt": wt_struct,
        "structure_mt": mt_struct,
        "pca2_wt": pca_wt,
        "pca2_mt": pca_mt,
        "fallback_reason": None if wt["engine"] == "rna-fm" else (_MODEL_ERROR or "torch/transformers 未安装，使用确定性特征嵌入"),
    }


# --------------------------------------------------------------------------- #
# 后台任务
# --------------------------------------------------------------------------- #
def _update(job_id: str, **kw) -> None:
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        if job and job["status"] != "cancelled":
            job.update(kw)


def submit(kind: str, fn, *args) -> str:
    job_id = uuid.uuid4().hex[:12]
    event = threading.Event()
    with _JOB_LOCK:
        _JOBS[job_id] = {"id": job_id, "kind": kind, "status": "queued",
                         "progress": 0.0, "stage": "", "result": None, "error": None}
        _JOB_EVENTS[job_id] = event
        _JOB_ORDER.append(job_id)
        while len(_JOB_ORDER) > _MAX_JOBS:
            old = _JOB_ORDER.pop(0)
            _JOBS.pop(old, None)
            _JOB_EVENTS.pop(old, None)

    def runner():
        with _JOB_LOCK:
            if _JOBS.get(job_id, {}).get("status") == "cancelled":
                return
        _CURRENT_CANCEL.event = event
        _update(job_id, status="running", progress=0.05)
        try:
            result = fn(*args, progress=lambda p: _update(job_id, progress=round(p, 2)))
            _raise_if_cancelled()
            _update(job_id, status="done", progress=1.0, result=result)
        except JobCancelled:
            # 状态已由 cancel_job 置为 cancelled，无需再写结果
            pass
        except Exception as e:
            _update(job_id, status="error", error=str(e), progress=1.0)
        finally:
            _CURRENT_CANCEL.event = None

    _EXECUTOR.submit(runner)
    return job_id


def cancel_job(job_id: str) -> bool:
    """取消排队中或运行中的任务；运行中的任务会在下一个计算检查点中断。"""
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        if not job or job["status"] in ("done", "error", "cancelled"):
            return False
        job.update(status="cancelled", progress=1.0, error="任务已取消")
        event = _JOB_EVENTS.get(job_id)
        if event is not None:
            event.set()
        return True


def cancel_all() -> int:
    """取消所有排队中/运行中的任务（服务关闭时调用，避免优雅退出被长任务拖住）。"""
    with _JOB_LOCK:
        pending = [
            jid for jid, job in _JOBS.items() if job["status"] in ("queued", "running")
        ]
    return sum(1 for jid in pending if cancel_job(jid))


def get_job(job_id: str) -> Optional[dict]:
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return None
        data = dict(job)
        if data["status"] == "running" and _MODEL_STAGE:
            # 权重加载/下载阶段无法用百分比表达，单独给出阶段文案
            data["stage"] = _MODEL_STAGE
        if data["status"] == "queued":
            queued = [jid for jid in _JOB_ORDER
                      if _JOBS.get(jid, {}).get("status") == "queued"]
            data["queue_position"] = queued.index(job_id) + 1
            data["queue_length"] = len(queued)
        return data

