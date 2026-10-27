import json
import os


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["cache"]["ok"] is True
    assert body["cache"]["backend"] == ("redis" if os.environ["REDIS_URL"] else "memory")
    assert body["llm"]["configured"] is False
    assert body["vector_store"]["backend"].startswith(("qdrant", "numpy"))


def test_ingest_single_and_validation(client):
    r = client.post("/api/logs", json={"timestamp": "2024-05-01T12:00:00Z", "message": "hi", "service": "api",
                                       "severity": "warn", "region": "eu"})
    assert r.status_code == 201 and r.json()["accepted"] == 1
    log = client.get("/api/logs").json()["items"][0]
    assert log["severity"] == "WARNING" and log["metadata"]["region"] == "eu"
    assert log["timestamp"].endswith("Z")

    assert client.post("/api/logs", json={"message": "no timestamp"}).status_code == 422
    r = client.post("/api/logs", json={"timestamp": "garbage", "message": "x"})
    assert r.status_code == 422 and "timestamp" in r.json()["detail"]


def test_batch_reports_errors_and_duplicates(client):
    rec = {"timestamp": "2024-05-01T12:00:00Z", "message": "hello"}
    r = client.post("/api/logs/batch", json={"logs": [rec, rec, {"timestamp": "2024-05-01T12:00:01Z"}]})
    body = r.json()
    assert (body["accepted"], body["duplicates"], body["rejected"]) == (1, 1, 1)
    assert body["errors"][0] == {"index": 2, "error": "missing message", "record": json.dumps(
        {"timestamp": "2024-05-01T12:00:01Z"})}


def test_upload_jsonl_csv_and_malformed(client):
    jsonl = "\n".join(json.dumps({"ts": f"2024-05-01T12:00:0{i}Z", "level": "error", "service": "db",
                                  "msg": f"Query timeout {i}"}) for i in range(5)) + "\n{bad json"
    r = client.post("/api/logs/upload", files={"file": ("logs.jsonl", jsonl, "application/x-ndjson")})
    assert r.status_code == 201
    assert (r.json()["accepted"], r.json()["rejected"]) == (5, 1)

    csv_text = "timestamp,severity,service,message\n2024-05-01T12:01:00Z,INFO,api,ok\n,INFO,api,missing ts\n"
    r = client.post("/api/logs/upload", files={"file": ("logs.csv", csv_text, "text/csv")})
    assert (r.json()["accepted"], r.json()["rejected"]) == (1, 1)

    r = client.post("/api/logs/upload", files={"file": ("x.json", "{not json", "application/json")})
    assert r.status_code == 422

    r = client.post("/api/logs/upload", files={"file": ("x.json", "", "application/json")})
    assert r.status_code == 400


def test_log_filters(client):
    logs = [{"timestamp": f"2024-05-01T12:00:{i:02d}Z", "service": "api" if i % 2 else "db",
             "severity": "ERROR" if i % 3 == 0 else "INFO", "message": f"event number {i}", "trace_id": f"t{i}"}
            for i in range(30)]
    client.post("/api/logs/batch", json={"logs": logs})
    assert client.get("/api/logs", params={"service": "api"}).json()["total"] == 15
    assert client.get("/api/logs", params={"severity": "error"}).json()["total"] == 10
    assert client.get("/api/logs", params={"q": "number 7"}).json()["total"] == 1
    r = client.get("/api/logs", params={"start": "2024-05-01T12:00:10Z", "end": "2024-05-01T12:00:19Z"})
    assert r.json()["total"] == 10
    assert client.get("/api/logs", params={"trace_id": "t4"}).json()["items"][0]["message"] == "event number 4"
    page = client.get("/api/logs", params={"limit": 5, "offset": 5, "order": "asc"}).json()
    assert len(page["items"]) == 5 and page["items"][0]["message"] == "event number 5"
    facets = client.get("/api/logs/facets").json()
    assert {s["name"] for s in facets["services"]} == {"api", "db"}


def test_end_to_end_generate_analyze_and_investigate(client):
    r = client.post("/api/samples/generate", json={"scenario": "db_timeout", "minutes": 60, "seed": 3,
                                                   "end_time": "2024-05-01T13:00:00Z", "analyze": True})
    assert r.status_code == 201
    analysis = r.json()["analysis"]
    assert analysis["incidents"] >= 1 and analysis["anomalies_detected"] >= 3

    incidents = client.get("/api/incidents").json()
    top = max(incidents, key=lambda i: i["confidence"])
    detail = client.get(f"/api/incidents/{top['id']}").json()
    assert detail["root_cause"]["primary"]["service"] == "orders-db"
    assert detail["evidence_logs"] and detail["anomalies"]
    assert detail["narrative"].startswith("### What happened")
    assert client.get(f"/api/incidents/{top['id']}/timeline").json()

    exp = client.post(f"/api/incidents/{top['id']}/explain", json={"use_llm": True}).json()
    assert exp["narrative_source"] == "deterministic" and exp["warning"]

    r = client.patch(f"/api/incidents/{top['id']}", json={"status": "resolved", "notes": "added index"})
    assert r.json()["status"] == "resolved"

    # A second, similar incident should be retrieved by vector search.
    client.post("/api/samples/generate", json={"scenario": "db_timeout", "minutes": 60, "seed": 4,
                                               "end_time": "2024-05-01T15:00:00Z", "analyze": False})
    client.post("/api/analysis/run", json={"start": "2024-05-01T13:30:00Z"})
    newest = client.get("/api/incidents").json()[0]
    assert newest["id"] != top["id"]
    similar = client.get(f"/api/incidents/{newest['id']}/similar").json()
    assert similar and similar[0]["id"] == top["id"] and similar[0]["notes"] == "added index"

    ov = client.get("/api/stats/overview").json()
    assert ov["total_logs"] > 0 and ov["incidents"]["resolved"] == 1
    ts = client.get("/api/stats/timeseries", params={"bucket_seconds": 300}).json()
    assert ts["points"] and ts["anomalies"]
    deps = client.get("/api/dependencies").json()
    assert {"source": "orders-service", "target": "orders-db"} in [
        {"source": e["source"], "target": e["target"]} for e in deps["edges"]]
    anomalies = client.get("/api/anomalies", params={"kind": "statistical"}).json()
    assert anomalies["total"] > 0 and all(a["kind"] == "statistical" for a in anomalies["items"])


def test_analysis_rejects_bad_range(client):
    r = client.post("/api/analysis/run", json={"start": "2024-05-02T00:00:00Z", "end": "2024-05-01T00:00:00Z"})
    assert r.status_code == 422


def test_signature_crud_and_test(client):
    assert len(client.get("/api/signatures").json()) >= 10
    body = {"key": "kafka_lag", "name": "Kafka consumer lag", "category": "dependency",
            "pattern": r"consumer lag \d+", "severity": "medium", "min_count": 2}
    assert client.post("/api/signatures", json=body).status_code == 201
    assert client.post("/api/signatures", json=body).status_code == 409
    assert client.post("/api/signatures", json={**body, "key": "bad_rx", "pattern": "(unclosed"}).status_code == 422
    r = client.put("/api/signatures/kafka_lag", json={"enabled": False})
    assert r.json()["enabled"] is False
    assert client.delete("/api/signatures/db_timeout").status_code == 400
    assert client.delete("/api/signatures/kafka_lag").status_code == 204
    t = client.post("/api/signatures/test", json={"pattern": "time(d)? ?out", "messages": ["Query timed out", "ok"]})
    assert [x["matched"] for x in t.json()["results"]] == [True, False]


def test_reset_requires_confirm(client):
    client.post("/api/logs", json={"timestamp": "2024-05-01T12:00:00Z", "message": "x"})
    assert client.post("/api/system/reset").status_code == 400
    assert client.post("/api/system/reset", params={"confirm": True}).json()["deleted_logs"] == 1
