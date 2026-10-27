# Sentinel RCA — AI Log Anomaly & Root-Cause Engine

Sentinel RCA ingests application and server logs, detects anomalies with robust statistics (MAD / robust z-scores) and configurable log signatures, correlates them into incidents, ranks the probable root causes with an evidence-weighted model, and shows everything in an interactive dashboard. An LLM (Groq) can write the narrative; without an API key a deterministic, evidence-only narrative is used.

Every conclusion links back to log evidence. The UI and API keep three kinds of statement apart:

| Kind | Meaning | Example |
|---|---|---|
| **Fact** | Directly observed in logs | "38 logs matched *Connection pool exhausted* in orders-db; first at 09:16:17" |
| **Signal** | Statistical deviation from baseline | "orders-db p95 latency 5000 ms vs. median 28 ms (robust z = 958)" |
| **Inference** | Probable cause derived from facts + signals + rules | "Connection pool exhaustion in orders-db (high confidence, 93%)" |

---

## Architecture

```
            ┌────────────── React + Vite + Tailwind + Recharts ──────────────┐
            │ Dashboard · Incidents · Incident RCA · Anomalies · Logs · ...   │
            └───────────────────────────────┬─────────────────────────────────┘
                                            │ REST + SSE (/api)
┌───────────────────────────────────────────▼──────────────────────────────────────────┐
│ FastAPI                                                                              │
│  ingestion ─► normalize/validate/dedupe ─► SQL (SQLite | PostgreSQL)                 │
│                                   │                                                  │
│  analysis pipeline:  load window ─► signature matcher ─► detectors                   │
│     • robust z (MAD) on per-service error counts, p95 latency, log-volume drops      │
│     • signature bursts (regex, min-count per window)                                 │
│  ─► dependency graph (from trace IDs) ─► correlation (union-find) ─► RCA scoring     │
│  ─► narrative (Groq LLM | deterministic) ─► vector index (Qdrant) for similarity     │
│  Redis: stats cache, correlation state, log stream mirror (in-process fallback)      │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

```
backend/
  app/
    main.py               FastAPI app, startup seeding
    config.py             env-driven settings
    models.py, schemas.py SQLAlchemy 2 models, Pydantic schemas
    api/                  logs, incidents/anomalies, signatures, analysis/stats/system routes
    services/
      ingestion.py        parsing (JSON/JSONL/CSV), field aliases, normalization, dedupe
      signatures.py       built-in + custom regex signatures
      detection.py        robust z-score / MAD detectors, signature bursts
      correlation.py      trace-inferred dependency graph, anomaly → incident grouping
      rca.py              facts, signals, causal chain, ranked hypotheses, timeline
      narrative.py        Groq LLM narrative + deterministic fallback
      embeddings.py       hash / sentence-transformers embeddings
      vector_store.py     Qdrant (server or embedded) + NumPy fallback
      cache.py            Redis cache/stream + in-process fallback, SSE broadcaster
      pipeline.py         end-to-end analysis orchestration
    data/generator.py     synthetic microservice log generator with failure scenarios
  scripts/generate_sample_data.py
  sample_data/            ready-made JSONL / JSON / CSV datasets
  tests/                  pytest suite (50 tests)
frontend/
  src/pages/              Dashboard, Incidents, IncidentDetail, Anomalies, Logs, Ingest, Signatures, System
  src/components/         Layout, LogTable, Timeline, ServiceMap, Markdown, UI primitives
```

## Quick start (local, no external services)

Requirements: Python 3.11+ and Node 18+. Redis, Qdrant, PostgreSQL and an LLM key are all optional: the app falls back to an in-process cache, embedded Qdrant on disk, SQLite and deterministic narratives.

**Backend**

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows; on macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # optional
uvicorn app.main:app --reload --port 8000
```

On first start with an empty database the backend seeds a two-hour synthetic "mixed" dataset (four incidents) and runs analysis. Set `SEED_SAMPLE_DATA=false` to skip this. API docs are at http://localhost:8000/docs.

**Frontend**

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The dev server proxies `/api` to `http://127.0.0.1:8000`; set `VITE_API_PROXY` to change the target, or `VITE_API_URL` to call the API directly.

**Tests**

```bash
cd backend
python -m pytest
```

By default the suite uses SQLite and the in-process fallbacks. To run the same suite against real servers, point it at a **dedicated** database (tables are dropped between tests):

```bash
TEST_DATABASE_URL=postgresql+psycopg://sentinel@127.0.0.1:5432/sentinel_test \
TEST_REDIS_URL=redis://127.0.0.1:6379/1 \
TEST_QDRANT_URL=http://127.0.0.1:6333 \
python -m pytest
```

All 51 tests pass in both modes. This was verified against PostgreSQL 16.11, Redis 5.0.14 and Qdrant 1.20.0.

## Docker Compose (PostgreSQL + Redis + Qdrant)

```bash
docker compose up --build
```

UI: http://localhost:8080 · API: http://localhost:8000/docs. To enable LLM narratives, export `GROQ_API_KEY` before running the command.

## Detection & analysis

**Robust z-score.** For each service, logs are bucketed into windows (default 60 s). For each series:

```
z = 0.6745 · (x − median) / MAD                 when MAD > 0
z = (x − median) / (1.253314 · MeanAD)          when MAD = 0  (common for sparse error counts)
```

| Series | Direction | Extra guard |
|---|---|---|
| error count (ERROR + CRITICAL) | spike | ≥ `MIN_ANOMALY_COUNT` errors in the window |
| p95 latency (`latency_ms`, `duration_ms`, … in metadata) | spike | ≥ 1.5 × baseline median |
| log volume | drop (service went quiet) | baseline median ≥ 5, ≤ 50% of median, edge windows excluded |

A series needs at least `MIN_BASELINE_WINDOWS` (8) windows before any statistical detection runs. Consecutive flagged windows are merged into one anomaly, which keeps its peak z-score, baseline, MAD and evidence log IDs.

**Signatures.** Fifteen built-in regex signatures cover database timeouts, pool exhaustion, deadlocks, HTTP 5xx, upstream timeouts, connection refused, memory pressure, OOM, crashes and restarts, unhandled exceptions, authentication failures, rate limiting, full disks, open circuit breakers and TLS errors. A signature fires when its matches in one window reach its `min_count`. A signature that fires at a steady background rate is downgraded one severity level. You can add, edit or disable signatures in the UI or through `/api/signatures`.

**Correlation.** Anomalies are linked when their windows are within `CORRELATION_GAP_SECONDS` of each other **and** at least one of these holds: they are on the same service, they share a trace ID, or their services are adjacent in the dependency graph. The dependency graph is inferred from trace IDs: A → B when A logs before B in at least two shared traces. Linking uses union-find. Low-severity anomalies can join a group but never bridge two groups, so background noise cannot chain unrelated incidents. A singleton anomaly below `high` severity is left uncorrelated.

**Root-cause ranking.** Each affected service gets a hypothesis score:

| Factor | Weight | Evidence |
|---|---|---|
| Temporal precedence | 0.30 | onset = first problem log inside the service's own anomaly windows |
| Trace propagation | 0.25 | share of cross-service error traces where this service failed first |
| Dependency depth | 0.15 | how many affected services call it (directly or transitively) |
| Cause-category prior | 0.15 | database/resource/storage ≈ cause; HTTP 5xx/circuit breaker ≈ symptom |
| Signal strength | 0.15 | strongest anomaly score / severity |

Confidence is the score, discounted when the runner-up is close and raised slightly when a causal link is confirmed by shared traces. Causal rules (for example `db_pool_exhausted → db_timeout → http_5xx`, `memory_pressure → out_of_memory → service_crash → connection_refused`) are applied only when the cause was seen first. Each rule lists its lag, shared trace IDs and evidence logs. Each hypothesis records its factor values, reasoning, recommended actions, and supporting anomaly, log and trace IDs.

**Re-analysis.** Re-running analysis over a time range replaces that range's anomalies. Incidents detected again keep their ID, status and notes. An incident matches first on an exact fingerprint (start minute and services). If that fails, it matches on an overlapping time window with service-set Jaccard similarity ≥ 0.5. This way a shifted baseline that adds or drops a peripheral service doesn't create a new incident.

**Similar incidents.** Each incident is embedded (title, services, signatures, root cause, causal chain, first error messages) and stored in Qdrant. The incident page shows the nearest past incidents and their resolution notes. `EMBEDDING_PROVIDER=auto` uses sentence-transformers when installed (`pip install -r requirements-optional.txt`), otherwise a deterministic feature-hashing embedder.

**LLM narratives.** With `GROQ_API_KEY` set, "Explain with LLM" sends only the structured evidence (facts, signals, chain, top hypotheses, up to 15 sample logs) with instructions to cite evidence IDs and keep facts and inference separate. If no key is set or the call fails, the deterministic narrative is used and the UI explains why.

## Log format

Any JSON object or CSV row with these fields (aliases in parentheses). Unknown fields go into `metadata`.

| Field | Required | Aliases |
|---|---|---|
| `timestamp` | yes | `@timestamp`, `ts`, `time`, `datetime`; ISO-8601, epoch s/ms/µs/ns, common log formats |
| `message` | yes | `msg`, `log`, `text`, `event`, `body` |
| `severity` | no (INFO) | `level`, `lvl`, `levelname`; `warn`, `err`, `fatal`, syslog 0–7 … |
| `service` | no (`unknown`) | `service_name`, `app`, `application`, `component`, `logger` |
| `host` | no | `hostname`, `instance`, `node`, `pod` |
| `trace_id` | no | `traceId`, `trace`, `request_id`, `correlation_id` |
| `metadata` | no | `meta`, `attributes`, `fields`, `context` (object or JSON string in CSV) |

Exact duplicates (same timestamp, severity, service, host, trace and message) are skipped, both within a request and against stored logs. Invalid records are reported with their index and reason; valid records in the same request are still ingested.

## API overview

| Method & path | Purpose |
|---|---|
| `POST /api/logs` · `POST /api/logs/batch` · `POST /api/logs/upload` | Ingest one log, a batch, or a JSON/JSONL/CSV file (`?analyze=true` to analyze afterwards) |
| `GET /api/logs` | Filter by `service`, `severity`, `start`, `end`, `q`, `trace_id`, `ids`; paginate |
| `GET /api/logs/facets` · `GET /api/logs/stream` | Facet counts · Server-sent events for live ingest |
| `POST /api/analysis/run` · `GET /api/analysis/runs` | Run detection, correlation and RCA with optional parameters · run history |
| `GET /api/incidents` · `GET/PATCH /api/incidents/{id}` | List incidents · detail (root cause, timeline, evidence logs) · update status/notes |
| `GET /api/incidents/{id}/timeline` · `/similar` · `POST /explain` | Timeline · vector-similar incidents · regenerate narrative |
| `GET /api/anomalies` | Filter by service, kind, severity, incident, or uncorrelated only |
| `GET/POST/PUT/DELETE /api/signatures`, `POST /api/signatures/test` | Manage signatures and test regexes |
| `POST /api/samples/generate` | Generate and ingest a synthetic scenario |
| `GET /api/stats/overview` · `/api/stats/timeseries` · `/api/dependencies` · `/api/health` | Dashboard data and component health |

## Synthetic data

The generator simulates `api-gateway → auth-service`, `api-gateway → orders-service → {inventory-service, payments-service, orders-db}` with shared trace IDs and latency metadata. Scenarios: `normal`, `db_timeout`, `http_500`, `memory_pressure`, `auth_failure`, `cascading_failure`, `mixed`.

```bash
python scripts/generate_sample_data.py --scenario memory_pressure --minutes 60 --formats jsonl,csv
```

Tests check that each scenario's root cause is ranked first (orders-db, payments-service, inventory-service and auth-service respectively), and that normal traffic produces no incidents.

## Configuration

See [`backend/.env.example`](backend/.env.example). Key settings: `DATABASE_URL`, `REDIS_URL`, `QDRANT_URL` / `QDRANT_PATH`, `EMBEDDING_PROVIDER`, `GROQ_API_KEY`, `GROQ_MODEL`, `WINDOW_SECONDS`, `Z_THRESHOLD`, `MIN_ANOMALY_COUNT`, `CORRELATION_GAP_SECONDS`, `ANALYSIS_LOOKBACK_HOURS`, `SEED_SAMPLE_DATA`.

## Limitations

- Analysis runs on demand (or after ingest with `analyze=true`), not continuously.
- The live stream (SSE) is per process; Redis Streams mirrors ingest events (`sentinel:logs`) for external consumers.
- Embedded Qdrant (`QDRANT_PATH`) allows one process; use `QDRANT_URL` with several workers.
- Causal rules and category priors are heuristics. Confidence is a ranking aid, not a probability.
- Two independent failures that overlap in time and share services are merged into one incident. Both still appear as ranked hypotheses.
- The Redis client uses RESP2 (`protocol=2`), so Redis 5+ and RESP2-compatible servers work.
- Running a Qdrant server natively on Windows needs a short storage path. Deep paths hit the 260-character path limit and fail with "Gridstore IO error … path not found".
