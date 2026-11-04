"""RNAComposer API 集成（占位）。

RNAComposer（https://rnacomposer.cs.put.poznan.pl/）提供免费学术 REST API，
把 RNA 序列转成全原子 3D 结构（PDB）。使用前需注册学术 key。

本模块仅为脚手架：拿到 key 后实现 ``predict_tertiary()``。
详见项目构建方案阶段 4。
"""
from __future__ import annotations

from typing import Optional


def predict_tertiary(sequence: str, api_key: Optional[str] = None) -> dict:
    raise NotImplementedError(
        "RNAComposer 需要注册学术 key（见构建方案阶段 4）。"
        "当前三级结构走预置 PDB 库（backend/main.py 的 /api/tertiary）。"
    )