"""RNA 二级结构预测引擎（三级降级策略）。

1. **ViennaRNA Python 绑定**（``import RNA``，pip 安装 wheel 自带）——首选，
   给出真实 kcal/mol 的 MFE 与碱基配对概率（bpp）。
2. **ViennaRNA CLI**（系统 PATH 中的 ``RNAfold``）——若绑定额外不可用但装了 CLI。
3. **纯 Python Nussinov 降级 + Boltzmann 采样**——无 ViennaRNA 时兜底，
   能量为估计值（非 kcal/mol），响应标注 ``engine: "fallback"``。

当前项目环境已通过 ``pip install ViennaRNA``（2.7.0 wheel）启用第 1 级。
"""
from __future__ import annotations

import math
import re
import shutil
import subprocess
from typing import List, Optional, Tuple

try:  # ViennaRNA Python 绑定
    import RNA  # type: ignore
    _HAS_RNA = True
except Exception:  # pragma: no cover
    RNA = None  # type: ignore
    _HAS_RNA = False

_BASE_PAIRS = {("A", "U"), ("U", "A"), ("G", "C"), ("C", "G"), ("G", "U"), ("U", "G")}
_PAIR_SCORE = {"GC": 3.0, "CG": 3.0, "AU": 2.0, "UA": 2.0, "GU": 1.0, "UG": 1.0}

MIN_LOOP = 3                 # 发夹环最小未配对碱基数
MAX_SEQUENCE_LEN = 1000      # 实时折叠限长（防止超长序列 O(n³) 拖垮服务）
FALLBACK_MAX_LEN = 300       # Nussinov 降级限长
BOLTZMANN_MAX_LEN = 200      # 配对概率（Boltzmann 采样）降级限长
BOLTZMANN_SAMPLES = 150      # 采样次数
BPP_MAX_LEN = 300            # 真实 bpp 限长（JSON 体积控制）


# --------------------------------------------------------------------------- #
# 序列处理
# --------------------------------------------------------------------------- #
def normalize_sequence(raw: str) -> str:
    """去除空白并把 DNA 的 T 转成 RNA 的 U，统一大写。"""
    return "".join(raw.split()).upper().replace("T", "U")


def validate_sequence(seq: str) -> None:
    if not seq:
        raise ValueError("序列为空")
    bad = sorted({c for c in seq if c not in "AUCG"})
    if bad:
        raise ValueError(f"含有非法碱基: {bad}")


def _can_pair(i: int, j: int, seq: str) -> bool:
    return (seq[i], seq[j]) in _BASE_PAIRS and (j - i - 1) >= MIN_LOOP


def _score(i: int, j: int, seq: str) -> float:
    return _PAIR_SCORE.get(seq[i] + seq[j], 0.0)


def find_rnafold() -> Optional[str]:
    return shutil.which("RNAfold")


def engine_status() -> str:
    if _HAS_RNA:
        return "viennarna-python"
    if find_rnafold():
        return "viennarna-cli"
    return "fallback"


def engine_version() -> Optional[str]:
    if _HAS_RNA:
        return getattr(RNA, "__version__", "unknown")
    return None


# --------------------------------------------------------------------------- #
# 结构工具
# --------------------------------------------------------------------------- #
def _dot_bracket_from_pairs(pairs, n: int) -> str:
    s = ["."] * n
    for i, j in pairs:
        if i < j:
            s[i], s[j] = "(", ")"
    return "".join(s)


def dot_bracket_to_pairs(struct: str) -> List[Tuple[int, int]]:
    """把 dot-bracket 转成碱基对列表 [(i, j), ...]（0-based，i < j）。"""
    pairs: List[Tuple[int, int]] = []
    stack: List[int] = []
    for i, ch in enumerate(struct):
        if ch == "(":
            stack.append(i)
        elif ch == ")":
            if stack:
                pairs.append((stack.pop(), i))
    return pairs


# --------------------------------------------------------------------------- #
# 1) ViennaRNA Python 绑定
# --------------------------------------------------------------------------- #
def _fold_binding(seq: str) -> dict:
    struct, mfe = RNA.fold(seq)
    pairs = dot_bracket_to_pairs(struct)
    return {
        "structure": struct,
        "mfe": round(float(mfe), 2),
        "pairs": [[i, j] for i, j in pairs],
    }


def _bpp_binding(seq: str) -> List[List[float]]:
    n = len(seq)
    fc = RNA.fold_compound(seq)
    fc.pf()  # 配分函数 / 完整性
    bpp = fc.bpp()  # 1-based 配对概率矩阵
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(1, n + 1):
        for j in range(1, n + 1):
            v = bpp[i][j]
            if v >= 0.005:
                matrix[i - 1][j - 1] = round(float(v), 4)
    return matrix


# --------------------------------------------------------------------------- #
# 2) ViennaRNA CLI
# --------------------------------------------------------------------------- #
def _fold_vienna_cli(seq: str) -> dict:
    p = subprocess.run(
        ["RNAfold", "--noPS"],
        input=seq + "\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    if p.returncode != 0:
        raise RuntimeError((p.stderr or "RNAfold 执行失败").strip())

    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise RuntimeError("RNAfold 输出格式异常")

    parts = lines[-1].split()
    struct = parts[0] if parts else ""
    mfe = None
    if len(parts) >= 2:
        m = re.search(r"[-+]?\d+(?:\.\d+)?", parts[1])
        if m:
            mfe = float(m.group(0))
    return {
        "structure": struct,
        "mfe": mfe,
        "pairs": [list(x) for x in dot_bracket_to_pairs(struct)],
    }


# --------------------------------------------------------------------------- #
# 3) 纯 Python 降级：Nussinov 最大得分结构
# --------------------------------------------------------------------------- #
def _nussinov(seq: str) -> Tuple[float, List[Tuple[int, int]]]:
    n = len(seq)
    g = [[0.0] * n for _ in range(n)]
    tr = [[None] * n for _ in range(n)]

    for L in range(1, n):
        for i in range(n - L):
            j = i + L
            best = -1.0
            choice = None
            for k in range(i, j):
                v = g[i][k] + g[k + 1][j]
                if v > best:
                    best = v
                    choice = ("split", k)
            if _can_pair(i, j, seq):
                v = _score(i, j, seq) + g[i + 1][j - 1]
                if v > best:
                    best = v
                    choice = ("pair",)
            g[i][j] = best
            tr[i][j] = choice

    pairs: List[Tuple[int, int]] = []

    def rec(i: int, j: int) -> None:
        if i >= j:
            return
        c = tr[i][j]
        if c is None:
            return
        if c[0] == "split":
            rec(i, c[1])
            rec(c[1] + 1, j)
        else:
            pairs.append((i, j))
            rec(i + 1, j - 1)

    rec(0, n - 1)
    return g[0][n - 1], pairs


def _pairing_probability(seq: str, samples: int = BOLTZMANN_SAMPLES, seed: int = 0):
    import random

    n = len(seq)
    if n > BOLTZMANN_MAX_LEN:
        raise ValueError(f"配对概率降级限长 {BOLTZMANN_MAX_LEN} nt（当前 {n} nt）")

    Q = [[0.0] * n for _ in range(n)]
    for i in range(n):
        Q[i][i] = 1.0

    def getq(i: int, j: int) -> float:
        return 1.0 if i > j else Q[i][j]

    for L in range(1, n):
        for i in range(n - L):
            j = i + L
            total = getq(i + 1, j)
            for k in range(i + 1, j + 1):
                if _can_pair(i, k, seq):
                    total += math.exp(_score(i, k, seq)) * getq(i + 1, k - 1) * getq(k + 1, j)
            Q[i][j] = total

    rng = random.Random(seed)
    freq = [[0] * n for _ in range(n)]

    def sample(i: int, j: int) -> None:
        if i >= j:
            return
        total = getq(i, j)
        r = rng.random() * total
        acc = getq(i + 1, j)
        if r < acc:
            sample(i + 1, j)
            return
        for k in range(i + 1, j + 1):
            if _can_pair(i, k, seq):
                acc += math.exp(_score(i, k, seq)) * getq(i + 1, k - 1) * getq(k + 1, j)
            if r < acc:
                freq[i][k] += 1
                sample(i + 1, k - 1)
                sample(k + 1, j)
                return
        sample(i + 1, j)

    for _ in range(samples):
        sample(0, n - 1)

    return [[round(freq[i][j] / samples, 4) for j in range(n)] for i in range(n)]


def _fold_fallback(seq: str) -> dict:
    n = len(seq)
    if n > FALLBACK_MAX_LEN:
        raise ValueError(
            f"未检测到 ViennaRNA，纯 Python 降级限长 {FALLBACK_MAX_LEN} nt（当前 {n} nt）"
        )
    score, pairs = _nussinov(seq)
    return {
        "structure": _dot_bracket_from_pairs(pairs, n),
        "mfe": round(-score, 2),
        "mfe_note": "降级引擎估计值（非 kcal/mol），仅供参考",
        "pairs": [list(x) for x in sorted(pairs)],
    }


# --------------------------------------------------------------------------- #
# 对外入口
# --------------------------------------------------------------------------- #
def fold(sequence: str) -> dict:
    seq = normalize_sequence(sequence)
    validate_sequence(seq)
    if len(seq) > MAX_SEQUENCE_LEN:
        raise ValueError(
            f"序列长度 {len(seq)} nt 超过实时折叠限长 {MAX_SEQUENCE_LEN} nt"
        )

    result = None
    engine = None

    if _HAS_RNA:
        try:
            result = _fold_binding(seq)
            engine = "viennarna-python"
        except Exception:
            result = None
    if result is None and find_rnafold():
        try:
            result = _fold_vienna_cli(seq)
            engine = "viennarna-cli"
        except Exception:
            result = None
    if result is None:
        result = _fold_fallback(seq)
        engine = "fallback"

    result["engine"] = engine
    result["sequence_length"] = len(seq)
    result["num_pairs"] = len(result["pairs"])
    return result


def pair_probability(sequence: str) -> dict:
    seq = normalize_sequence(sequence)
    validate_sequence(seq)

    if _HAS_RNA:
        if len(seq) > BPP_MAX_LEN:
            raise ValueError(f"配对概率限长 {BPP_MAX_LEN} nt（当前 {len(seq)} nt）")
        return {
            "engine": "viennarna-bpp",
            "sequence": seq,
            "matrix": _bpp_binding(seq),
        }

    if len(seq) > BOLTZMANN_MAX_LEN:
        raise ValueError(f"配对概率限长 {BOLTZMANN_MAX_LEN} nt（当前 {len(seq)} nt）")
    return {
        "engine": "boltzmann-sample",
        "sequence": seq,
        "matrix": _pairing_probability(seq),
    }