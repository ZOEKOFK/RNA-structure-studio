"""RNAComposer 手动桥接。

RNAComposer（https://rnacomposer.cs.put.poznan.pl/）是免费的公开服务，
**没有 API key，也没有公开 REST API**：交互模式匿名可用（≤500 nt），
批量模式只需注册普通账号。因此本平台不模拟登录抓取，而是生成可直接
粘贴到网页的输入文本，由用户在本站下载 PDB 后回填查看。
"""
from __future__ import annotations

from typing import Optional

RNACOMPOSER_URL = "https://rnacomposer.cs.put.poznan.pl/"
RNACOMPOSER_MAX_LEN = 500


def build_input(sequence: str, structure: Optional[str] = None, task_id: str = "rna") -> str:
    """生成交互模式输入：第 1 行任务名、第 2 行序列、可选第 3 行 dot-bracket。"""
    lines = [f">{task_id}", sequence]
    if structure:
        lines.append(structure)
    return "\n".join(lines)
