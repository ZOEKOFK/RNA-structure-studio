"""核心算法与深度层缓存的单元测试（不依赖网络、不加载真实权重）。"""
import json
import os

import pytest

from backend import composer_api, rcsb, rna_fm, rna_fold


# --------------------------------------------------------------------------- #
# 序列规范化与校验
# --------------------------------------------------------------------------- #
def test_normalize_plain_sequence_converts_dna():
    assert rna_fold.normalize_sequence("augcTT") == "AUGCUU"


def test_normalize_fasta_ignores_header_and_whitespace():
    raw = ">tRNA\ngggaaaccc\n;comment\nGGGAAACCC\n"
    assert rna_fold.normalize_sequence(raw) == "GGGAAACCCGGGAAACCC"


def test_validate_rejects_empty():
    with pytest.raises(ValueError, match="序列为空"):
        rna_fold.validate_sequence("")


def test_validate_rejects_degenerate_bases():
    with pytest.raises(ValueError, match="简并碱基"):
        rna_fold.validate_sequence("AUGCN")


def test_validate_rejects_illegal_chars():
    with pytest.raises(ValueError, match="非法字符"):
        rna_fold.validate_sequence("AUGC1")


# --------------------------------------------------------------------------- #
# 二级结构
# --------------------------------------------------------------------------- #
def test_dot_bracket_to_pairs_roundtrip():
    struct = "((..))"
    pairs = rna_fold.dot_bracket_to_pairs(struct)
    assert pairs == [(1, 4), (0, 5)]
    assert rna_fold._dot_bracket_from_pairs(pairs, len(struct)) == struct


def test_fold_returns_balanced_structure():
    result = rna_fold.fold("GGGAAACCC")
    struct = result["structure"]
    assert len(struct) == 9
    assert struct.count("(") == struct.count(")")
    assert result["sequence_length"] == 9
    assert result["num_pairs"] == len(result["pairs"])


def test_fold_enforces_max_length():
    with pytest.raises(ValueError, match="超过实时折叠限长"):
        rna_fold.fold("A" * (rna_fold.MAX_SEQUENCE_LEN + 1))


# --------------------------------------------------------------------------- #
# 深度层降级嵌入 / 缓存
# --------------------------------------------------------------------------- #
def test_fallback_embedding_dimension():
    vectors = rna_fm._fallback_vectors("GGGAAACCC", "(((...)))")
    assert len(vectors) == 9
    assert len(vectors[0]) == 34


def test_embed_uses_cache_on_second_call(monkeypatch, tmp_path):
    monkeypatch.setattr(rna_fm, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(rna_fm, "_MODEL_ERROR", None)
    monkeypatch.setattr(
        rna_fm, "_real_vectors", lambda seq: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    first = rna_fm.embed("GGGAAACCC")
    second = rna_fm.embed("GGGAAACCC")

    assert first["engine"] == "fallback"
    assert first["dim"] == 34
    assert first["source"] == "compute"
    assert second["source"] == "cache"
    assert second["dim"] == first["dim"]


def test_cache_prune_keeps_recent_files(monkeypatch, tmp_path):
    monkeypatch.setattr(rna_fm, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(rna_fm, "_CACHE_MAX_FILES", 3)

    seqs = ["AAAA", "AAAU", "AAAG", "AAAC", "AAGA"]
    for i, seq in enumerate(seqs):
        vectors = rna_fm._fallback_vectors(seq, "." * len(seq))
        rna_fm._write_cache(seq, "fallback", "." * len(seq), vectors)
        os.utime(rna_fm._cache_path(seq, "fallback"), (1000 + i, 1000 + i))

    rna_fm._prune_cache()
    remaining = [p for p in os.listdir(tmp_path) if p.endswith(".json")]
    assert len(remaining) == 3

    newest = os.path.basename(rna_fm._cache_path(seqs[-1], "fallback"))
    assert newest in remaining
    oldest = os.path.basename(rna_fm._cache_path(seqs[0], "fallback"))
    assert oldest not in remaining


def test_read_cache_refreshes_mtime(monkeypatch, tmp_path):
    monkeypatch.setattr(rna_fm, "_CACHE_DIR", str(tmp_path))
    vectors = rna_fm._fallback_vectors("GGGAAACCC", "(((...)))")
    rna_fm._write_cache("GGGAAACCC", "fallback", "(((...)))", vectors)
    path = rna_fm._cache_path("GGGAAACCC", "fallback")
    os.utime(path, (1000, 1000))

    assert rna_fm._read_cache("GGGAAACCC", "fallback") is not None
    assert os.stat(path).st_mtime > 1000


def test_cache_key_depends_on_engine(monkeypatch, tmp_path):
    monkeypatch.setattr(rna_fm, "_CACHE_DIR", str(tmp_path))
    assert rna_fm._cache_path("GGGAAACCC", "rna-fm") != rna_fm._cache_path("GGGAAACCC", "fallback")


def test_embed_rejects_sequence_over_limit():
    with pytest.raises(ValueError, match="深度分析限长"):
        rna_fm.embed("A" * (rna_fm.MAX_DEEP_LEN + 1))


# --------------------------------------------------------------------------- #
# 任务取消
# --------------------------------------------------------------------------- #
def test_cancel_queued_job():
    job_id = "test-cancel-job"
    with rna_fm._JOB_LOCK:
        rna_fm._JOBS[job_id] = {
            "id": job_id, "kind": "embed", "status": "queued",
            "progress": 0.0, "result": None, "error": None,
        }
        rna_fm._JOB_ORDER.append(job_id)

    try:
        assert rna_fm.cancel_job(job_id) is True
        job = rna_fm.get_job(job_id)
        assert job["status"] == "cancelled"
        assert rna_fm.cancel_job(job_id) is False
    finally:
        with rna_fm._JOB_LOCK:
            rna_fm._JOBS.pop(job_id, None)
            if job_id in rna_fm._JOB_ORDER:
                rna_fm._JOB_ORDER.remove(job_id)


def test_get_job_reports_queue_position():
    first, second = "test-queue-1", "test-queue-2"
    with rna_fm._JOB_LOCK:
        for jid in (first, second):
            rna_fm._JOBS[jid] = {
                "id": jid, "kind": "embed", "status": "queued",
                "progress": 0.0, "result": None, "error": None,
            }
            rna_fm._JOB_ORDER.append(jid)

    try:
        assert rna_fm.get_job(second)["queue_position"] == 2
        assert rna_fm.get_job(second)["queue_length"] == 2
    finally:
        with rna_fm._JOB_LOCK:
            for jid in (first, second):
                rna_fm._JOBS.pop(jid, None)
                if jid in rna_fm._JOB_ORDER:
                    rna_fm._JOB_ORDER.remove(jid)


def test_cache_payload_has_engine_and_structure(monkeypatch, tmp_path):
    monkeypatch.setattr(rna_fm, "_CACHE_DIR", str(tmp_path))
    vectors = rna_fm._fallback_vectors("GGGAAACCC", "(((...)))")
    rna_fm._write_cache("GGGAAACCC", "fallback", "(((...)))", vectors)

    with open(rna_fm._cache_path("GGGAAACCC", "fallback"), encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["engine"] == "fallback"
    assert payload["structure"] == "(((...)))"
    assert payload["dim"] == 34


# --------------------------------------------------------------------------- #
# RCSB 序列检索 / RNAComposer 输入
# --------------------------------------------------------------------------- #
def test_rcsb_search_parses_hits_and_caches(monkeypatch):
    rcsb._CACHE.clear()
    calls = {"count": 0}

    def fake_post(url, payload, timeout):
        calls["count"] += 1
        return {
            "result_set": [
                {"identifier": "1EHZ", "score": 1.0},
                {"identifier": "1EVV", "score": 0.95},
            ]
        }

    monkeypatch.setattr(rcsb, "_post_json", fake_post)
    monkeypatch.setattr(
        rcsb,
        "_get_json",
        lambda url, timeout: {
            "struct": {"title": "tRNA"},
            "rcsb_entry_info": {"resolution_combined": [2.0], "experimental_method": "X-ray"},
        },
    )

    seq = "ACGUACGUACGUACGU"
    hits = rcsb.search_similar(seq)
    assert [h["pdb_id"] for h in hits] == ["1EHZ", "1EVV"]
    assert hits[0]["resolution"] == 2.0
    assert hits[0]["method"] == "X-ray"
    assert hits[0]["url"] == "https://www.rcsb.org/structure/1EHZ"

    rcsb.search_similar(seq)
    assert calls["count"] == 1, "第二次查询应命中缓存"
    rcsb._CACHE.clear()


def test_rcsb_failure_is_not_cached(monkeypatch):
    rcsb._CACHE.clear()
    seq = "ACGUACGUACGUACGU"

    def boom(url, payload, timeout):
        raise TimeoutError("network down")

    monkeypatch.setattr(rcsb, "_post_json", boom)
    with pytest.raises(TimeoutError):
        rcsb.search_similar(seq)
    assert seq not in rcsb._CACHE


def test_composer_input_format():
    text = composer_api.build_input("GGGAAACCC", "(((...)))")
    assert text.splitlines() == [">rna", "GGGAAACCC", "(((...)))"]
    assert composer_api.build_input("GGGAAACCC") == ">rna\nGGGAAACCC"
