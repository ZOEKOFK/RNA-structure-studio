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


def test_examples_include_rfam_annotation():
    examples = client.get("/api/examples").json()["examples"]
    annotated = [ex for ex in examples if ex.get("rfam")]
    assert annotated, "示例库应至少有一条带 Rfam 家族注释"
    assert all(ex["rfam"]["accession"].startswith("RF") for ex in annotated)
    assert all(ex["rfam"]["consensus"] for ex in annotated)


def test_pdb_endpoint_serves_local_coordinates():
    res = client.get("/api/pdb/4TNA")
    if res.status_code == 404:
        pytest.skip("data/pdb 未收录 4TNA")
    assert res.status_code == 200
    assert "ATOM" in res.text


def test_pdb_endpoint_rejects_malformed_id():
    assert client.get("/api/pdb/TOOLONG").status_code == 400
    assert client.get("/api/pdb/4T").status_code == 400


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


def test_tertiary_rate_limited(monkeypatch):
    from backend import main

    main._RATE_HITS.clear()
    monkeypatch.setattr(main, "_RATE_LIMIT", 2)
    monkeypatch.setattr("backend.rcsb.search_similar", lambda seq: [])

    url = "/api/tertiary"
    body = {"sequence": "GGGAAACCCGGGAAACCC"}
    assert client.post(url, json=body).status_code == 200
    assert client.post(url, json=body).status_code == 200
    res = client.post(url, json=body)
    assert res.status_code == 429
    assert "过于频繁" in res.json()["detail"]
    assert res.headers["Retry-After"]
    main._RATE_HITS.clear()


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


# --------------------------------------------------------------------------- #
# 反向代理与优雅关闭
# --------------------------------------------------------------------------- #
def _make_request(xff=None, client_host="10.0.0.1"):
    from starlette.requests import Request

    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "headers": headers, "client": (client_host, 1234)})


def test_client_ip_ignores_forwarded_header_by_default(monkeypatch):
    from backend import main

    monkeypatch.delenv("RNA_TRUST_PROXY", raising=False)
    req = _make_request(xff="1.2.3.4, 5.6.7.8")
    assert main._client_ip(req) == "10.0.0.1", "未显式信任代理时不能采信 X-Forwarded-For"


def test_client_ip_honors_forwarded_header_when_trusted(monkeypatch):
    from backend import main

    monkeypatch.setenv("RNA_TRUST_PROXY", "1")
    assert main._client_ip(_make_request(xff="1.2.3.4, 5.6.7.8")) == "1.2.3.4"
    assert main._client_ip(_make_request()) == "10.0.0.1"


def test_cancel_all_cancels_only_unfinished_jobs():
    import threading

    with rna_fm._JOB_LOCK:
        rna_fm._JOBS.clear()
        rna_fm._JOB_EVENTS.clear()
        rna_fm._JOB_ORDER.clear()
        base = {"kind": "embed", "progress": 0.0, "result": None, "error": None}
        rna_fm._JOBS["q"] = {**base, "id": "q", "status": "queued"}
        rna_fm._JOBS["r"] = {**base, "id": "r", "status": "running"}
        rna_fm._JOBS["d"] = {**base, "id": "d", "status": "done"}
        rna_fm._JOB_EVENTS["r"] = threading.Event()

    assert rna_fm.cancel_all() == 2
    assert rna_fm.get_job("q")["status"] == "cancelled"
    assert rna_fm.get_job("r")["status"] == "cancelled"
    assert rna_fm.get_job("d")["status"] == "done"

    with rna_fm._JOB_LOCK:
        rna_fm._JOBS.clear()
        rna_fm._JOB_EVENTS.clear()
        rna_fm._JOB_ORDER.clear()


def test_multi_worker_config_is_rejected(monkeypatch):
    from backend import main

    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    with pytest.raises(RuntimeError, match="单进程"):
        main._check_single_process()

    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    main._check_single_process()


def test_examples_reload_when_file_changes(monkeypatch, tmp_path):
    from backend import main

    path = tmp_path / "examples.json"
    path.write_text('[{"id": "a"}]', encoding="utf-8")
    monkeypatch.setattr(main, "_examples_path", lambda: str(path))
    monkeypatch.setitem(main._EXAMPLES_CACHE, "mtime", None)
    assert len(main._load_examples()) == 1

    path.write_text('[{"id": "a"}, {"id": "b"}]', encoding="utf-8")
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert len(main._load_examples()) == 2


# --------------------------------------------------------------------------- #
# 本地 3D 建模
# --------------------------------------------------------------------------- #
def _wait_job(url, timeout=30.0):
    """轮询任务直到终态，返回最后一次响应体。"""
    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        job = client.get(url).json()
        if job["status"] in ("done", "error", "cancelled"):
            return job
        time.sleep(0.1)
    return job


def _atom_coords(pdb_text, atom):
    coords = []
    for line in pdb_text.splitlines():
        if line.startswith("ATOM") and line[12:16].strip() == atom:
            coords.append(
                (float(line[30:38]), float(line[38:46]), float(line[46:54]))
            )
    return coords


def test_health_reports_3d_engine(monkeypatch):
    from backend import rna3d

    monkeypatch.setattr(rna3d, "_CMD_TEMPLATE", "")
    data = client.get("/api/health").json()
    assert data["engine_3d"] == "builtin"
    assert data["model_3d_max_len"] == rna3d.MAX_3D_LEN
    assert data["model_3d_note"]


def test_3d_predict_builds_and_serves_model(monkeypatch, tmp_path):
    from backend import rna3d

    monkeypatch.setattr(rna3d, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(rna3d, "_CMD_TEMPLATE", "")

    res = client.post(
        "/api/3d/predict", json={"sequence": EXAMPLE_SEQ, "structure": "(((...)))"}
    )
    assert res.status_code == 200
    submitted = res.json()
    assert submitted["status"] == "queued"
    assert submitted["structure"] == "(((...)))"
    assert submitted["engine"] == "builtin"

    job = _wait_job(f"/api/3d/jobs/{submitted['job_id']}")
    assert job["status"] == "done", job.get("error")
    result = job["result"]
    assert result["engine"] == "builtin"
    assert result["fallback"] is None
    assert result["atoms"] == 6 * len(EXAMPLE_SEQ)
    assert result["structure"] == "(((...)))"

    model = client.get(result["url"])
    assert model.status_code == 200
    assert "ATOM" in model.text
    assert "SIMPLIFIED RNA MODEL" in model.text


def test_3d_predict_uses_predicted_structure_by_default(monkeypatch, tmp_path):
    from backend import rna3d

    monkeypatch.setattr(rna3d, "_CACHE_DIR", str(tmp_path))
    res = client.post("/api/3d/predict", json={"sequence": EXAMPLE_SEQ})
    assert res.status_code == 200
    structure = res.json()["structure"]
    assert len(structure) == len(EXAMPLE_SEQ)
    assert set(structure) <= set(".()")

    job = _wait_job(f"/api/3d/jobs/{res.json()['job_id']}")
    assert job["status"] == "done", job.get("error")
    assert job["result"]["structure"] == structure


def test_3d_predict_reuses_cache(monkeypatch, tmp_path):
    from backend import rna3d

    monkeypatch.setattr(rna3d, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(rna3d, "_CMD_TEMPLATE", "")
    body = {"sequence": EXAMPLE_SEQ, "structure": "(((...)))"}

    first = _wait_job(f"/api/3d/jobs/{client.post('/api/3d/predict', json=body).json()['job_id']}")
    second = _wait_job(f"/api/3d/jobs/{client.post('/api/3d/predict', json=body).json()['job_id']}")
    assert first["result"]["model_id"] == second["result"]["model_id"]
    assert second["result"]["cached"] is True


def test_3d_predict_rejects_structure_length_mismatch(monkeypatch):
    from backend import rna3d

    monkeypatch.setattr(rna3d, "_CMD_TEMPLATE", "")
    res = client.post("/api/3d/predict", json={"sequence": EXAMPLE_SEQ, "structure": "(())"})
    assert res.status_code == 400
    assert "长度" in res.json()["detail"]


def test_3d_predict_rejects_invalid_structure_chars(monkeypatch):
    from backend import rna3d

    monkeypatch.setattr(rna3d, "_CMD_TEMPLATE", "")
    res = client.post("/api/3d/predict", json={"sequence": EXAMPLE_SEQ, "structure": "(((" + "X" * 6})
    assert res.status_code == 400
    assert "dot-bracket" in res.json()["detail"]


def test_3d_predict_rejects_oversize_sequence(monkeypatch):
    from backend import rna3d

    res = client.post("/api/3d/predict", json={"sequence": "A" * (rna3d.MAX_3D_LEN + 1)})
    assert res.status_code == 400
    assert "限长" in res.json()["detail"]


def test_3d_predict_rejects_degenerate_base(monkeypatch):
    from backend import rna3d

    res = client.post("/api/3d/predict", json={"sequence": "AUGCN"})
    assert res.status_code == 400
    assert "简并碱基" in res.json()["detail"]


def test_3d_job_and_model_not_found():
    assert client.get("/api/3d/jobs/does-not-exist").status_code == 404
    assert client.delete("/api/3d/jobs/does-not-exist").status_code == 404
    assert client.get("/api/3d/models/does-not-exist").status_code == 404
    # 非 sha1 形态的 ID 不落盘查找
    assert client.get("/api/3d/models/" + "z" * 40).status_code == 404


def test_3d_cancel_unknown_job():
    assert client.delete("/api/3d/jobs/no-such-job").status_code == 404


def test_3d_external_engine_falls_back_to_builtin(monkeypatch, tmp_path):
    from backend import rna3d

    monkeypatch.setattr(rna3d, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(rna3d, "_CMD_TEMPLATE", "definitely-not-a-command {out_pdb}")
    monkeypatch.setattr(rna3d, "_run_external", lambda seq, ss: None)

    _model_id, meta = rna3d.build_model(EXAMPLE_SEQ, "(((...)))")
    assert meta["engine"] == "builtin"
    assert meta["fallback"] and "回退" in meta["fallback"]


def test_builtin_model_geometry_is_reasonable():
    from backend import rna3d

    seq, structure = "GGGAAACCC", "(((...)))"
    pdb = rna3d.build_builtin(seq, structure)
    atom_names = [line[12:16].strip() for line in pdb.splitlines() if line.startswith("ATOM")]
    # 每残基完整糖-磷酸骨架（Mol* 需要 O3'/C3' 才能连成核酸卡通表示）
    assert len(atom_names) == 6 * len(seq)
    assert atom_names[:6] == ["P", "O5'", "C5'", "C4'", "C3'", "O3'"]
    for name in ("P", "O5'", "C5'", "C4'", "C3'", "O3'"):
        assert len(_atom_coords(pdb, name)) == len(seq), f"每个残基应输出一个 {name} 原子"

    coords = _atom_coords(pdb, "P")

    def dist(a, b):
        return sum((a[k] - b[k]) ** 2 for k in range(3)) ** 0.5

    pairs = [(0, 8), (1, 7), (2, 6)]
    pair_dist = sum(dist(coords[i], coords[j]) for i, j in pairs) / len(pairs)
    assert abs(pair_dist - rna3d._PAIR_DIST) < 1.0, f"碱基对平均距离 {pair_dist:.2f} Å"

    backbone = sum(dist(coords[i], coords[i + 1]) for i in range(len(seq) - 1)) / (len(seq) - 1)
    assert abs(backbone - rna3d._BACKBONE) < 1.0, f"骨架平均间距 {backbone:.2f} Å"

    # 相邻骨架原子（含 O3'→P(i+1)）必须落在成键距离区间，否则 Mol* 画不出连续链
    for i in range(len(seq) - 1):
        atoms = [_atom_coords(pdb, name)[i] for name in ("P", "O5'", "C5'", "C4'", "C3'", "O3'")]
        atoms.append(_atom_coords(pdb, "P")[i + 1])
        for a, b in zip(atoms, atoms[1:]):
            gap = dist(a, b)
            assert 1.1 <= gap <= 2.1, f"第 {i + 1} 个残基相邻原子间距 {gap:.2f} Å 不合理"

