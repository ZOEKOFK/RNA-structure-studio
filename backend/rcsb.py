"""RCSB PDB 序列相似度检索（公开 Search API，无需 key）。

官方文档：https://search.rcsb.org/ 与 https://data.rcsb.org/
用于在预置结构库未命中时，自动找出最相近的已解析 RNA 实验结构。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

SEARCH_URL = "https://search.rcsb.org/rcsbsearch/v2/query"
ENTRY_URL = "https://data.rcsb.org/rest/v1/core/entry/{pdb_id}"
MAX_CACHE = 100              # 已查序列的结果缓存条数
DEFAULT_TOP = 5
USER_AGENT = "rna-fold/0.1 (RNA structure prediction platform)"
_RETRIES = 2                 # 网络波动时的重试次数（不含首次）
_RETRY_BACKOFF = 0.4         # 秒，按尝试次数线性递增

_LOCK = threading.Lock()
_CACHE: "OrderedDict[Tuple[str, int, float], List[dict]]" = OrderedDict()


def _fetch_json(req: urllib.request.Request, timeout: float) -> dict:
    """GET/POST 并解析 JSON；对 5xx / 429 / 网络错误做有限重试。"""
    last: Optional[Exception] = None
    for attempt in range(_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                # RCSB 在零命中时返回 204 No Content（空响应体），不是错误
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            # 4xx（除 429）属于请求本身有问题，重试无意义
            if e.code < 500 and e.code != 429:
                raise
            last = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
        if attempt < _RETRIES:
            time.sleep(_RETRY_BACKOFF * (attempt + 1))
    assert last is not None
    raise last


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    return _fetch_json(req, timeout)


def _get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
    )
    return _fetch_json(req, timeout)


def _first(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _entry(pdb_id: str, score: Optional[float], timeout: float) -> dict:
    """拉取条目摘要；元数据失败时仍返回可用条目（至少能打开 RCSB 页面）。"""
    hit = {
        "pdb_id": pdb_id,
        "score": round(float(score), 3) if score is not None else None,
        "title": None,
        "resolution": None,
        "method": None,
        "url": f"https://www.rcsb.org/structure/{pdb_id}",
    }
    try:
        data = _get_json(ENTRY_URL.format(pdb_id=pdb_id), timeout)
    except Exception:
        return hit
    hit["title"] = (data.get("struct") or {}).get("title")
    info = data.get("rcsb_entry_info") or {}
    hit["resolution"] = _first(info.get("resolution_combined"))
    hit["method"] = _first(info.get("experimental_method"))
    return hit


def search_similar(
    sequence: str,
    top: int = DEFAULT_TOP,
    identity_cutoff: float = 0.9,
    timeout: float = 8.0,
) -> List[dict]:
    """按序列相似度检索 RCSB PDB，返回实验结构列表（按得分降序）。

    网络不可达或服务异常会抛出异常，由调用方决定是否降级。
    """
    key = (sequence, top, identity_cutoff)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached

    payload = {
        "query": {
            "type": "terminal",
            "service": "sequence",
            "parameters": {
                "evalue_cutoff": 1,
                "identity_cutoff": identity_cutoff,
                "sequence_type": "rna",
                "value": sequence,
            },
        },
        "return_type": "entry",
        "request_options": {"paginate": {"start": 0, "rows": top}},
    }
    data = _post_json(SEARCH_URL, payload, timeout)
    results = [(r.get("identifier"), r.get("score")) for r in data.get("result_set") or []]
    results = [(pid, score) for pid, score in results if pid]

    hits: List[dict] = []
    if results:
        with ThreadPoolExecutor(max_workers=min(4, len(results))) as pool:
            hits = list(pool.map(lambda item: _entry(item[0], item[1], timeout), results))
    hits.sort(key=lambda h: (h["score"] is None, -(h["score"] or 0.0)))

    with _LOCK:
        _CACHE[key] = hits
        _CACHE.move_to_end(key)
        while len(_CACHE) > MAX_CACHE:
            _CACHE.popitem(last=False)
    return hits
