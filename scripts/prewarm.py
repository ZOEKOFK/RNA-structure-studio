"""演示预热：把示例库的折叠结果与深度嵌入写入缓存，避免现场首次请求等待。

用法（在 rna-fold 目录下）：
    python scripts/prewarm.py            # 折叠 + 深度嵌入 + 补齐本地 PDB
    python scripts/prewarm.py --skip-deep  # 只预热折叠与 PDB

缓存目录由 RNA_CACHE_DIR 决定（与后端一致，默认 data/cache/embeddings）。
"""
import argparse
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend import rna_fm, rna_fold  # noqa: E402

EXAMPLES = os.path.join(ROOT, "data", "examples", "examples.json")
PDB_DIR = os.path.join(ROOT, "data", "pdb")
PDB_URL = "https://files.rcsb.org/download/{}.pdb"
UA = {"User-Agent": "rna-fold/0.1"}


def ensure_pdb(pdb_id: str) -> str:
    os.makedirs(PDB_DIR, exist_ok=True)
    path = os.path.join(PDB_DIR, f"{pdb_id.upper()}.pdb")
    if os.path.isfile(path):
        return "已存在"
    req = urllib.request.Request(PDB_URL.format(pdb_id.upper()), headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp, open(path, "wb") as f:
        f.write(resp.read())
    return f"下载 {os.path.getsize(path) // 1024} KB"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-deep", action="store_true", help="跳过深度嵌入预热")
    args = parser.parse_args()

    with open(EXAMPLES, encoding="utf-8") as f:
        examples = json.load(f)

    print(f"示例 {len(examples)} 条 | 折叠引擎 {rna_fold.engine_status()} | 深度引擎 {rna_fm.engine_status()}")
    print(f"嵌入缓存目录：{rna_fm._CACHE_DIR}\n")

    failures = 0
    for ex in examples:
        name, seq = ex["name"], ex["sequence"]
        started = time.time()

        fold = rna_fold.fold(seq)
        fold_note = f"MFE {fold['mfe']}"

        pdb_note = "无 PDB"
        if ex.get("pdb"):
            try:
                pdb_note = f"PDB {ex['pdb']} {ensure_pdb(ex['pdb'])}"
            except Exception as exc:  # noqa: BLE001
                pdb_note = f"PDB {ex['pdb']} 下载失败：{exc}"

        deep_note = "跳过深度"
        if not args.skip_deep:
            if len(seq) > rna_fm.MAX_DEEP_LEN:
                deep_note = f"超出深度限长 {rna_fm.MAX_DEEP_LEN}"
            else:
                try:
                    emb = rna_fm.embed(seq)
                    deep_note = f"嵌入 {emb['dim']} 维 · {emb['engine']}"
                except Exception as exc:  # noqa: BLE001
                    failures += 1
                    deep_note = f"嵌入失败：{exc}"

        print(f"[{time.time() - started:5.1f}s] {name} | {len(seq)} nt | {fold_note} | {deep_note} | {pdb_note}")

    print(f"\n完成，失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
