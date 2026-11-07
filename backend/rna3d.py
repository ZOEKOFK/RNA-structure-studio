"""本地 3D 建模：离线从「序列 + 二级结构」生成可查看的粗粒度模型。

两条路径：

1. **内置简化建模器**（默认，纯 Python、零依赖）——把 dot-bracket 当成约束，
   用环形初值 + 位置约束松弛（backbone 5.9 Å、碱基对 10.4 Å）求解 P 坐标，
   再沿 P(i)–P(i+1) 弦按真实键长铺出完整糖-磷酸骨架（P / O5' / C5' / C4' /
   C3' / O3'），Mol* 可识别为核酸聚合物并绘制骨架带状表示。秒级完成，
   用于教学演示与离线预览，**精度远低于 RNAComposer / RhoFold+**，前端会明确标注。
2. **外部命令钩子**（可选）——``RNA_3D_CMD`` 给出模板，支持占位符
   ``{seq_file}`` / ``{ss_file}`` / ``{out_pdb}`` / ``{workdir}``，
   可接 SimRNA、RhoFold+ 等自行安装的引擎；失败时回退内置建模器。

任务在后台单线程执行，结果按内容哈希落盘缓存。
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


MAX_3D_LEN = _env_int("RNA_MAX_3D_LEN", 500)
_CMD_TEMPLATE = os.environ.get("RNA_3D_CMD", "").strip()
_CMD_TIMEOUT = _env_int("RNA_3D_TIMEOUT", 600)   # 外部引擎单任务超时（秒）
# 缓存根目录与深度层保持一致（容器内 /app/data 只读，必须落到 RNA_CACHE_DIR 的命名卷）
_CACHE_ROOT = os.environ.get("RNA_CACHE_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "cache"
)
_CACHE_DIR = os.environ.get("RNA_3D_CACHE_DIR") or os.path.join(_CACHE_ROOT, "models3d")

_BACKBONE = 5.9      # 相邻核苷酸（P 原子）间距（Å）
_PAIR_DIST = 10.4    # 典型 RNA 碱基对（配对 P 原子）距离（Å）
_RELAX_ITERS = 240
_BUILTIN_VERSION = 4  # 内置建模算法版本；改动几何后自增，旧缓存自动失效

# 四类括号各自独立配对栈，可表达伪结（如 ..[[...]]... 处 ( 与 [ 交叉）
_BRACKET_OPEN = {"(": ")", "[": "]", "{": "}", "<": ">"}


def parse_pairs(structure: str) -> Tuple[List[Tuple[int, int]], bool]:
    """解析 dot-bracket 为配对列表（0-based，i < j），支持 () [] {} <> 四类括号。

    返回 (pairs, has_pseudoknot)。伪结判定：存在交叉对，即
    存在 (i, j) 与 (k, l) 满足 i < k < j < l。
    不匹配的括号（无对应开括号）忽略，与主折叠模块行为一致。
    """
    stacks: Dict[str, List[int]] = {ch: [] for ch in _BRACKET_OPEN}
    pairs: List[Tuple[int, int]] = []
    for i, ch in enumerate(structure):
        if ch in stacks:
            stacks[ch].append(i)
        else:
            for open_ch, close_ch in _BRACKET_OPEN.items():
                if ch == close_ch and stacks[open_ch]:
                    pairs.append((stacks[open_ch].pop(), i))
                    break
    pairs.sort()
    has_pk = False
    for a in range(len(pairs)):
        i1, j1 = pairs[a]
        for b in range(a + 1, len(pairs)):
            i2, j2 = pairs[b]
            if i2 >= j1:
                break
            if i1 < i2 < j1 < j2:
                has_pk = True
                break
        if has_pk:
            break
    return pairs, has_pk


def pseudoknot_note(structure: str) -> Optional[str]:
    """结构含伪结时给出提示文案；无伪结返回 None。"""
    _, has_pk = parse_pairs(structure)
    if not has_pk:
        return None
    return (
        "检测到伪结：交叉配对已按普通距离约束一并建模，"
        "但内置简化模型无法表达真实伪结拓扑，走向仅供参考"
    )

_EXECUTOR = ThreadPoolExecutor(max_workers=1)
_JOB_LOCK = threading.Lock()
_JOBS: Dict[str, dict] = {}
_JOB_ORDER: List[str] = []
_MAX_JOBS = 50


class JobCancelled(Exception):
    """协作式取消信号。"""


_CURRENT_CANCEL = threading.local()


def _raise_if_cancelled() -> None:
    event = getattr(_CURRENT_CANCEL, "event", None)
    if event is not None and event.is_set():
        raise JobCancelled()


# --------------------------------------------------------------------------- #
# 内置简化建模
# --------------------------------------------------------------------------- #
def _depth(structure: str) -> List[int]:
    """每个位置的嵌套深度（茎区高、环区低），用于给平面布局抬升 z。"""
    depth: List[int] = []
    level = 0
    for ch in structure:
        if ch == "(":
            depth.append(level)
            level += 1
        elif ch == ")":
            level = max(0, level - 1)
            depth.append(level)
        else:
            depth.append(level)
    return depth


def _initial_layout(n: int, structure: str) -> List[List[float]]:
    """环形初值：碱基按 5'→3' 排布在圆周上，嵌套越深越向内收并抬高。

    环半径按骨架周长反推（5.9 Å/nt），但设 40 Å 上限：不设上限时 76 nt 的
    tRNA 会摊成半径 71 Å 的大圆盘（Rg≈53 Å，真实 tRNA 约 23 Å），Mol* 里
    就是一只又大又扁的「蜘蛛」。压到 40 Å 后由嵌套深度同时向内收、向上抬，
    结构变紧凑且真正有三维层次。
    """
    depth = _depth(structure)
    ideal = _BACKBONE * n / (2 * math.pi)
    radius = max(9.0, min(ideal, 40.0))
    pts: List[List[float]] = []
    for i in range(n):
        theta = 2 * math.pi * i / n
        r = radius - 2.0 * min(depth[i], 6)
        pts.append([r * math.cos(theta), r * math.sin(theta), 6.0 * min(depth[i], 8)])
    return pts


def _pull(pts: List[List[float]], a: int, b: int, target: float, weight: float) -> None:
    """把 a、b 两点沿连线方向调整为距离 target（各承担一半修正）。"""
    dx = pts[b][0] - pts[a][0]
    dy = pts[b][1] - pts[a][1]
    dz = pts[b][2] - pts[a][2]
    dist = math.sqrt(dx * dx + dy * dy + dz * dz) or 1e-6
    k = (dist - target) / dist * 0.5 * weight
    for idx, sign in ((a, 1.0), (b, -1.0)):
        pts[idx][0] += sign * dx * k
        pts[idx][1] += sign * dy * k
        pts[idx][2] += sign * dz * k


def _relax(pts: List[List[float]], pairs: List[Tuple[int, int]]) -> List[List[float]]:
    """位置约束松弛：骨架等长 + 碱基对固定距离 + 轻微回拉初值防塌缩。"""
    n = len(pts)
    anchors = [p[:] for p in pts]
    for _ in range(_RELAX_ITERS):
        _raise_if_cancelled()
        # 先满足碱基对，再用骨架等长约束收敛；回拉初值的权重必须很小，
        # 否则环形初值会把已经闭合的碱基对重新拉开。
        for i, j in pairs:
            _pull(pts, i, j, _PAIR_DIST, 1.0)
        for i in range(n - 1):
            _pull(pts, i, i + 1, _BACKBONE, 1.0)
        for i in range(n):
            for axis in range(3):
                pts[i][axis] += (anchors[i][axis] - pts[i][axis]) * 0.005
    return pts


_AA = {"A": "A", "U": "U", "G": "G", "C": "C"}

# 糖-磷酸骨架原子及沿 P→P 弧的累积距离（Å）。键长取自标准核苷酸几何：
# P–O5' 1.60、O5'–C5' 1.45、C5'–C4' 1.52、C4'–C3' 1.53、C3'–O3' 1.42，
# 末端的 O3'–P(i+1) 约 1.58，整段弧长 9.10 Å 对应 P–P 弦长 5.9 Å。
_BACKBONE_ATOMS = (
    ("P", "P", 0.00),
    ("O5'", "O", 1.60),
    ("C5'", "C", 3.05),
    ("C4'", "C", 4.57),
    ("C3'", "C", 6.10),
    ("O3'", "O", 7.52),
)
_BACKBONE_ARC = 9.10


def _dist(a: List[float], b: List[float]) -> float:
    return math.sqrt(sum((a[k] - b[k]) ** 2 for k in range(3)))


def _norm(v: List[float]) -> List[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1e-6
    return [x / n for x in v]


def _direction(a: List[float], b: List[float]) -> List[float]:
    return _norm([b[k] - a[k] for k in range(3)])


def _perpendicular(hint: List[float], axis: List[float]) -> List[float]:
    """hint 在垂直于 axis 的平面上的投影；退化时用全局轴叉乘兜底。"""
    dot = sum(hint[k] * axis[k] for k in range(3))
    v = [hint[k] - dot * axis[k] for k in range(3)]
    if math.sqrt(sum(x * x for x in v)) > 1e-6:
        return _norm(v)
    ref = [1.0, 0.0, 0.0] if abs(axis[0]) < 0.9 else [0.0, 1.0, 0.0]
    return _norm([
        axis[1] * ref[2] - axis[2] * ref[1],
        axis[2] * ref[0] - axis[0] * ref[2],
        axis[0] * ref[1] - axis[1] * ref[0],
    ])


def _backbone_points(p0, p1, hint) -> List[List[float]]:
    """沿 P(i)→P(i+1) 弦按真实键长铺 6 个骨架原子。

    单段需容纳 9.1 Å 弧长却只有 5.9 Å 弦长，若走半圆弧主链会在弦两侧各摆
    ~3 Å，Mol* 的卡通带随之剧烈抖动。这里改成绕弦的小半径螺旋（半径约
    1.1 Å）：轴向位移随弧长线性增长，周向转一整圈，既不偏离弦太多，又让
    相邻原子间距落在 1.3–1.6 Å 的成键区间。
    """
    chord = _dist(p0, p1)
    if chord < 1e-3:
        axis = [1.0, 0.0, 0.0]
        chord = _BACKBONE
    else:
        axis = _direction(p0, p1)
    side = _perpendicular(hint, axis)
    tilt = _norm([
        axis[1] * side[2] - axis[2] * side[1],
        axis[2] * side[0] - axis[0] * side[2],
        axis[0] * side[1] - axis[1] * side[0],
    ])
    scale = chord / _BACKBONE
    arc = _BACKBONE_ARC * scale
    radius = math.sqrt(max(arc * arc - chord * chord, 0.0)) / (2 * math.pi)
    points: List[List[float]] = []
    for _name, _elem, offset in _BACKBONE_ATOMS:
        travelled = offset * scale
        along = travelled / arc          # 0 → p0，1 → p1
        phi = 2 * math.pi * along
        # 半径两端收敛到 0，保证段首尾精确落在 P(i)、P(i+1) 上，
        # 相邻残基的 O3'→P(i+1) 才不会因相位突变而忽长忽短
        bulge = radius * math.sin(math.pi * along)
        points.append([
            p0[k] + axis[k] * chord * along
            + bulge * (math.cos(phi) * side[k] + math.sin(phi) * tilt[k])
            for k in range(3)
        ])
    return points


def _pdb(seq: str, structure: str, pts: List[List[float]]) -> str:
    """输出完整糖-磷酸骨架（P/O5'/C5'/C4'/C3'/O3'），Mol* 能识别为核酸链。"""
    n = len(seq)
    pair_map: Dict[int, int] = {}
    for i, j in parse_pairs(structure)[0]:
        pair_map[i], pair_map[j] = j, i
    lines: List[str] = [
        "REMARK   SIMPLIFIED RNA MODEL (constraint-based, teaching grade)",
        "REMARK   BACKBONE ONLY: P O5* C5* C4* C3* O3* PER RESIDUE",
        f"REMARK   LENGTH {n}  PAIRS {len(pair_map) // 2}",
        "TITLE     simplified_model",
    ]
    serial = 0
    hint = [0.0, 0.0, 1.0]
    for i in range(n):
        p0 = pts[i]
        if i + 1 < n:
            p1 = pts[i + 1]
        elif i > 0:
            axis = _direction(pts[i - 1], pts[i])
            p1 = [p0[k] + axis[k] * _BACKBONE for k in range(3)]
        else:
            p1 = [p0[0] + _BACKBONE, p0[1], p0[2]]
        partner = pair_map.get(i)
        if partner is not None:
            hint = _direction(p0, pts[partner])
        atoms = _backbone_points(p0, p1, hint)
        res = _AA.get(seq[i], "A")
        for (name, elem, _offset), xyz in zip(_BACKBONE_ATOMS, atoms):
            serial += 1
            lines.append(
                f"ATOM  {serial:5d} {' ' + name:<4s} {res:<3s} A{i + 1:4d}    "
                f"{xyz[0]:8.3f}{xyz[1]:8.3f}{xyz[2]:8.3f}  1.00  0.00"
                f"{' ' * 10}{elem:>2s}"
            )
    lines.append("END")
    return "\n".join(lines) + "\n"


def build_builtin(seq: str, structure: str) -> str:
    """内置建模器：返回 PDB 文本。"""
    pairs = parse_pairs(structure)[0]
    pts = _initial_layout(len(seq), structure)
    pts = _relax(pts, pairs)
    return _pdb(seq, structure, pts)


# --------------------------------------------------------------------------- #
# 外部命令钩子
# --------------------------------------------------------------------------- #
def _run_external(seq: str, structure: str) -> Optional[str]:
    """执行 RNA_3D_CMD 模板，成功后返回 PDB 文本；未配置或失败返回 None。"""
    if not _CMD_TEMPLATE:
        return None
    workdir = tempfile.mkdtemp(prefix="rna3d_")
    try:
        seq_file = os.path.join(workdir, "input.fasta")
        ss_file = os.path.join(workdir, "input.dotbracket")
        out_pdb = os.path.join(workdir, "model.pdb")
        with open(seq_file, "w", encoding="utf-8") as f:
            f.write(f">query\n{seq}\n")
        with open(ss_file, "w", encoding="utf-8") as f:
            f.write(structure + "\n")
        cmd = (
            _CMD_TEMPLATE.replace("{seq_file}", seq_file)
            .replace("{ss_file}", ss_file)
            .replace("{out_pdb}", out_pdb)
            .replace("{workdir}", workdir)
        )
        proc = subprocess.run(
            cmd, shell=True, cwd=workdir, timeout=_CMD_TIMEOUT,
            capture_output=True, text=True,
        )
        if proc.returncode != 0 or not os.path.isfile(out_pdb):
            return None
        with open(out_pdb, encoding="utf-8", errors="replace") as f:
            text = f.read()
        return text if "ATOM" in text or "HETATM" in text else None
    except Exception:
        return None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 缓存
# --------------------------------------------------------------------------- #
def _cache_key(seq: str, structure: str, engine: str) -> str:
    # 内置建模器改动算法后必须让旧缓存失效，否则用户会继续看到旧模型
    version = f"v{_BUILTIN_VERSION}" if engine == "builtin" else "external"
    raw = f"{engine}:{version}\x00{seq}\x00{structure}".encode()
    return hashlib.sha1(raw).hexdigest()


def _cache_path(key: str) -> str:
    return os.path.join(_CACHE_DIR, f"{key}.pdb")


def _read_cache(key: str) -> Optional[str]:
    path = _cache_path(key)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _write_cache(key: str, pdb: str) -> None:
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_cache_path(key), "w", encoding="utf-8") as f:
            f.write(pdb)
    except OSError:
        pass    # 缓存失败不影响主流程


def model_path(model_id: str) -> Optional[str]:
    """按模型 ID（sha1）定位缓存文件，非法 ID 返回 None。"""
    if not re.fullmatch(r"[0-9a-f]{40}", model_id or ""):
        return None
    path = _cache_path(model_id)
    return path if os.path.isfile(path) else None


# --------------------------------------------------------------------------- #
# 建模主流程
# --------------------------------------------------------------------------- #
def build_model(seq: str, structure: str) -> Tuple[str, dict]:
    """返回 (模型 ID, 元信息)。优先外部引擎，失败回退内置建模器。"""
    engine = "external" if _CMD_TEMPLATE else "builtin"
    key = _cache_key(seq, structure, engine)
    started = time.time()

    cached = _read_cache(key)
    if cached is not None:
        return key, {"engine": "external" if engine == "external" else "builtin",
                     "cached": True, "elapsed": 0.0, "atoms": cached.count("\nATOM"),
                     "fallback": None}

    fallback: Optional[str] = None
    pdb: Optional[str] = None
    if engine == "external":
        _raise_if_cancelled()
        pdb = _run_external(seq, structure)
        if pdb is None:
            fallback = "外部 3D 引擎未产出坐标（命令失败或输出缺失），已回退内置简化建模"
    if pdb is None:
        _raise_if_cancelled()
        engine = "builtin"
        key = _cache_key(seq, structure, engine)
        cached = _read_cache(key)
        if cached is not None:
            return key, {"engine": "builtin", "cached": True, "elapsed": 0.0,
                         "atoms": cached.count("\nATOM"), "fallback": fallback}
        pdb = build_builtin(seq, structure)

    _write_cache(key, pdb)
    return key, {
        "engine": engine,
        "cached": False,
        "elapsed": round(time.time() - started, 2),
        "atoms": pdb.count("\nATOM"),
        "fallback": fallback,
    }


def engine_detail() -> dict:
    return {
        "engine_3d": "external" if _CMD_TEMPLATE else "builtin",
        "model_3d_max_len": MAX_3D_LEN,
        "model_3d_note": (
            "外部 3D 引擎已配置（RNA_3D_CMD），失败时回退内置简化建模"
            if _CMD_TEMPLATE else
            "内置简化建模（教学用粗粒度模型，精度低于 RNAComposer / RhoFold+）"
        ),
    }


# --------------------------------------------------------------------------- #
# 任务表
# --------------------------------------------------------------------------- #
def _run_job(job_id: str, seq: str, structure: str, cancel: threading.Event) -> None:
    _CURRENT_CANCEL.event = cancel
    try:
        job = _JOBS.get(job_id)
        if job is None:
            return
        job.update(status="running", stage="正在生成 3D 坐标…")
        _raise_if_cancelled()
        model_id, meta = build_model(seq, structure)
        _raise_if_cancelled()
        job.update(status="done", stage="", result={
            "model_id": model_id,
            "url": f"/api/3d/models/{model_id}",
            "sequence": seq,
            "structure": structure,
            **meta,
        })
    except JobCancelled:
        job = _JOBS.get(job_id)
        if job is not None:
            job.update(status="cancelled", stage="")
    except Exception as e:  # pragma: no cover - 环境相关
        job = _JOBS.get(job_id)
        if job is not None:
            job.update(status="error", stage="", error=f"{type(e).__name__}: {e}")
    finally:
        _CURRENT_CANCEL.event = None


def submit(seq: str, structure: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    cancel = threading.Event()
    with _JOB_LOCK:
        if len(_JOB_ORDER) >= _MAX_JOBS:
            oldest = _JOB_ORDER.pop(0)
            _JOBS.pop(oldest, None)
        _JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage": "排队中…",
            "result": None,
            "error": None,
            "created": time.time(),
            "_cancel": cancel,
        }
        _JOB_ORDER.append(job_id)
    _EXECUTOR.submit(_run_job, job_id, seq, structure, cancel)
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    job = _JOBS.get(job_id)
    if job is None:
        return None
    return {k: v for k, v in job.items() if not k.startswith("_")}


def cancel_job(job_id: str) -> bool:
    """取消排队/运行中的任务；已结束返回 False。"""
    job = _JOBS.get(job_id)
    if job is None or job["status"] not in ("queued", "running"):
        return False
    cancel = job.get("_cancel")
    if cancel is not None:
        cancel.set()
    job.update(status="cancelled", stage="")
    return True


def cancel_all() -> int:
    count = 0
    for job_id in list(_JOBS):
        if cancel_job(job_id):
            count += 1
    return count
