"""FastAPI 接口层的冒烟与回归测试。"""
import os
import time

import pytest
from fastapi.testclient import TestClient

from backend import rna_fm
from backend.main import app

client = TestClient(app)

EXAMPLE_SEQ = "GGGAAACCC"


# --------------------------------------------------------------------------- #
# 基础接口
# --------------------------------------------------------------------------- #
def test_health():
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["engine"] in ("viennarna-python", "viennarna-cli", "fallback")
    assert data["deep_engine"] in ("rna-fm", "fallback")
    assert data["deep_max_len"] == rna_fm.MAX_DEEP_LEN
    assert "deep_cache_max" in data


def test_examples():
    res = client.get("/api/examples")
    assert res.status_code == 200
    examples = res.json()["examples"]
    assert len(examples) > 0
    assert all("sequence" in ex and "name" in ex for ex in examples)


def test_fold_ok():
    res = client.post("/api/fold", json={"sequence": EXAMPLE_SEQ})
    assert res.status_code == 200
    data = res.json()
    assert len(data["structure"]) == len(EXAMPLE_SEQ)


def test_fold_accepts_fasta_and_dna():
    res = client.post("/api/fold", json={"sequence": ">demo\nGGGTTTCCC\n"})
    assert res.status_code == 200
    assert res.json()["sequence_length"] == 9


def test_fold_rejects_degenerate_base():
    res = client.post("/api/fold", json={"sequence": "AUGCN"})
    assert res.status_code == 400
    assert "简并碱基" in res.json()["detail"]


def test_fold_rejects_empty_sequence():
    res = client.post("/api/fold", json={"sequence": "  \n"})
    assert res.status_code == 400


def test_fold_rejects_oversize_sequence():
    res = client.post("/api/fold", json={"sequence": "A" * 1001})
    assert res.status_code == 400


def test_pairprob_ok():
    res = client.post("/api/pairprob", json={"sequence": EXAMPLE_SEQ})
    assert res.status_code == 200
    data = res.json()
    assert len(data["matrix"]) == len(EXAMPLE_SEQ)


def test_mutate_ok():
    res = client.post("/api/mutate", json={"sequence": EXAMPLE_SEQ, "position": 1, "base": "U"})
    assert res.status_code == 200
    data = res.json()
    assert data["mutated"][0] == "U"
    assert isinstance(data["delta_mfe"], float)


def test_mutate_out_of_range():
    res = client.post("/api/mutate", json={"sequence": EXAMPLE_SEQ, "position": 99, "base": "U"})
    assert res.status_code == 400


def test_mutate_invalid_base():
    res = client.post("/api/mutate", json={"sequence": EXAMPLE_SEQ, "position": 1, "base": "X"})
    assert res.status_code == 400


def test_tertiary_matches_prebuilt_example():
    examples = client.get("/api/examples").json()["examples"]
    target = next((ex for ex in examples if ex.get("pdb")), None)
    if target is None:
        pytest.skip("示例库中没有带 PDB 的分子")
    res = client.post("/api/tertiary", json={"sequence": target["sequence"]})
    assert res.status_code == 200
    data = res.json()
    assert data["matched"] is True
    assert data["pdb"] == target["pdb"]


def test_tertiary_unmatched_returns_message(monkeypatch):
    monkeypatch.setattr("backend.rcsb.search_similar", lambda seq: [])
    res = client.post("/api/tertiary", json={"sequence": "GGGAAACCCGGGAAACCC"})
    assert res.status_code == 200
    data = res.json()
    assert data["matched"] is False
    assert data["similar"] == []
    assert "RNAComposer" in data["message"]
    assert data["structure"]
    assert data["rnacomposer"]["url"].startswith("https://rnacomposer")


def test_tertiary_returns_rcsb_similar_structures(monkeypatch):
    hits = [
        {
            "pdb_id": "1EHZ",
            "score": 1.0,
            "title": "酵母苯丙氨酸 tRNA 晶体结构",
            "resolution": 1.93,
            "method": "X-ray",
            "url": "https://www.rcsb.org/structure/1EHZ",
        }
    ]
    monkeypatch.setattr("backend.rcsb.search_similar", lambda seq: hits)
    res = client.post("/api/tertiary", json={"sequence": "GGGAAACCCGGGAAACCC"})
    assert res.status_code == 200
    data = res.json()
    assert data["source"] == "rcsb"
    assert data["pdb"] == "1EHZ"
    assert data["similar"][0]["resolution"] == 1.93


def test_tertiary_degrades_when_rcsb_unavailable(monkeypatch):
    def boom(seq):
        raise TimeoutError("network down")

    monkeypatch.setattr("backend.rcsb.search_similar", boom)
    res = client.post("/api/tertiary", json={"sequence": "GGGAAACCCGGGAAACCC"})
    assert res.status_code == 200
    data = res.json()
    assert data["matched"] is False
    assert data["similar"] == []
    assert "TimeoutError" in data["rcsb_error"]


def test_tertiary_prebuilt_includes_structure_and_bridge():
    examples = client.get("/api/examples").json()["examples"]
    target = next((ex for ex in examples if ex.get("pdb")), None)
    if target is None:
        pytest.skip("示例库中没有带 PDB 的分子")
    res = client.post("/api/tertiary", json={"sequence": target["sequence"]})
    data = res.json()
    assert data["matched"] is True
    assert data["structure"].count("(") == data["structure"].count(")")
    assert data["rnacomposer"]["input"].splitlines()[1] == target["sequence"]


# --------------------------------------------------------------------------- #
# 深度接口
# --------------------------------------------------------------------------- #
def test_deep_embed_rejects_degenerate_base():
    res = client.post("/api/deep/embed", json={"sequence": "AUGCN"})
    assert res.status_code == 400
    assert "简并碱基" in res.json()["detail"]


def test_deep_embed_rejects_oversize_sequence():
    res = client.post("/api/deep/embed", json={"sequence": "A" * (rna_fm.MAX_DEEP_LEN + 1)})
    assert res.status_code == 400
    assert "限长" in res.json()["detail"]


def test_deep_mutate_rejects_position_out_of_range():
    res = client.post("/api/deep/mutate", json={"sequence": EXAMPLE_SEQ, "position": 99, "base": "A"})
    assert res.status_code == 400


def test_deep_job_not_found():
    res = client.get("/api/deep/jobs/does-not-exist")
    assert res.status_code == 404


def test_deep_embed_job_completes_with_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(rna_fm, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(
        rna_fm, "_real_vectors", lambda seq: (_ for _ in ()).throw(RuntimeError("forced fallback"))
    )

    res = client.post("/api/deep/embed", json={"sequence": EXAMPLE_SEQ})
    assert res.status_code == 200
    job_id = res.json()["job_id"]

    deadline = time.time() + 30
    job = None
    while time.time() < deadline:
        job = client.get(f"/api/deep/jobs/{job_id}").json()
        if job["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.1)

    assert job is not None
    assert job["status"] == "done", job.get("error")
    assert job["result"]["engine"] == "fallback"
    assert job["result"]["dim"] == 34
    assert len(job["result"]["pca2"]) == len(EXAMPLE_SEQ)


def test_deep_job_cancel_endpoint():
    job_id = "api-cancel-job"
    with rna_fm._JOB_LOCK:
        rna_fm._JOBS[job_id] = {
            "id": job_id, "kind": "embed", "status": "queued",
            "progress": 0.0, "result": None, "error": None,
        }
        rna_fm._JOB_ORDER.append(job_id)

    try:
        position = client.get(f"/api/deep/jobs/{job_id}").json()
        assert position["queue_position"] >= 1

        res = client.delete(f"/api/deep/jobs/{job_id}")
        assert res.status_code == 200
        assert res.json()["status"] == "cancelled"

        again = client.delete(f"/api/deep/jobs/{job_id}")
        assert again.status_code == 409
    finally:
        with rna_fm._JOB_LOCK:
            rna_fm._JOBS.pop(job_id, None)
            if job_id in rna_fm._JOB_ORDER:
                rna_fm._JOB_ORDER.remove(job_id)


def test_deep_cancel_unknown_job():
    res = client.delete("/api/deep/jobs/does-not-exist")
    assert res.status_code == 404


# --------------------------------------------------------------------------- #
# 静态前端（存在 frontend/dist 时由后端托管）
# --------------------------------------------------------------------------- #
def _dist_dir():
    from backend import main

    return main._DIST_DIR


def test_spa_fallback_serves_index():
    if not os.path.isdir(_dist_dir()):
        pytest.skip("frontend/dist 不存在，跳过静态托管测试")

    res = client.get("/deep")
    assert res.status_code == 200
    assert '<div id="app">' in res.text


def test_unknown_api_path_is_404():
    res = client.get("/api/does-not-exist")
    assert res.status_code == 404

