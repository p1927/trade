# Phase 0 — End-to-End Map & Verification

**Goal:** Trace Run analysis from UI click to hub artifact and panel API response.

---

## Flow diagram

```
PredictionControls.onRun
  → useIndexPrediction.runAnalysis()
  → runPredictionAnalysis() [usePredictionRunCoordinator.ts]
  → POST /trade/index-prediction/run/start
  → start_job() + spawn_worker()
  → subprocess: index_prediction_run_worker <job_id>
  → run_worker() → run_index_research() → save_index_research()
  → _index_doc_to_panel(doc) → complete_job(artifact)
  → SSE GET /run/{job_id}/stream → UI sets runArtifact
  → GET /index-prediction (hub cache) merges with run artifact
```

---

## File 1: `PredictionControls.tsx`

**Snippet — Run button:**

```154:162:vibetrading/frontend/src/components/prediction/PredictionControls.tsx
        <button
          type="button"
          onClick={onRun}
          disabled={running}
          className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
        >
          {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
          {running ? "Analysis running…" : "Run analysis"}
```

**What it does:** Dispatches `onRun` from parent; disables while `running`. Checkbox **Refresh all 50 constituents** controls `refreshConstituents` (cached vs full 50-stock research).

**Data:** User inputs → `horizonDays`, `refreshConstituents`, `pollMs` (live refresh separate from full run).

**Correctness:** Matches north star — explicit user control over expensive constituent refresh (C6).

---

## File 2: `usePredictionRunCoordinator.ts`

**Snippet — API body and SSE attach:**

```264:277:vibetrading/frontend/src/hooks/usePredictionRunCoordinator.ts
  const body = {
    ticker: key,
    horizon_days: horizonDays,
    refresh_constituents: refreshConstituents,
    run_forecast_lab: true,
  };

  try {
    const start = await api.startIndexPredictionRun(body);
    if (gen !== attachGenRef.current) return;
    await attachToJob(key, start.job_id, gen, {
      reattach: Boolean(start.reused),
      clearLogs: true,
    });
```

**What it does:** Always requests `run_forecast_lab: true` (multi-track lab attached to run). Single-flight per ticker via backend `start_job` reuse. SSE streams logs until `onDone(artifact)`.

**Data in → out:** `{ticker, horizon_days, refresh_constituents, run_forecast_lab}` → `job_id` → `IndexPredictionArtifact` panel dict.

**Correctness:** Coordinator lives in Layout — run survives navigation. Fallback to legacy `POST /run/stream` on 404/405.

---

## File 3: `trade_routes.py`

**Endpoints:**

| Route | Role |
|-------|------|
| `POST /index-prediction/run/start` | Queue job (202 + job_id) |
| `GET /index-prediction/run/{job_id}/stream` | SSE logs + terminal artifact |
| `GET /index-prediction/run/active` | Reattach on page load |
| `POST /index-prediction/run` | Sync blocking run (legacy) |

**Correctness:** Async path preferred; sync path still calls same `run_index_research`.

---

## File 4: `index_prediction_run_jobs.py`

**Snippet — worker core:**

```494:516:vibetrading/agent/src/trade/index_prediction_run_jobs.py
        doc = run_index_research(
            key,
            horizon_days=horizon_days,
            refresh_constituents=refresh_constituents,
            run_forecast_lab=run_forecast_lab,
            pipeline=plog,
        )
        with plog.stage_timer("persist", "Save hub artifact"):
            save_index_research(doc)
        ...
        artifact = _index_doc_to_panel(doc)
        artifact["asset_type"] = "index"
        complete_job(job_id, ticker=key, artifact=artifact)
```

**What it does:** Blocking pipeline in subprocess; persists hub JSON + playground cache; stores **panel-shaped** artifact on job for SSE completion.

**Data:** Job file at `log/index_prediction_jobs/{job_id}/job.json`; TTL 1h for finished jobs; single active job per ticker.

**Correctness:** Zombie/stale reconciliation (wall clock 2700s, stale log 600s) prevents hung UI.

---

## File 5: `index_prediction_run_worker.py`

CLI entry: `python -m src.trade.index_prediction_run_worker <job_id>`. Detached via `spawn_worker` (`start_new_session=True`) so API hot-reload does not kill the run.

---

## File 6: `hub_bridge.py` — `_index_doc_to_panel`

**Snippet — API shape:**

```364:389:vibetrading/agent/src/trade/hub_bridge.py
    return {
        "ticker": doc.ticker,
        ...
        "prediction": pred,
        "regime": doc.regime or {},
        "scenarios": doc.scenarios or [],
        "factor_explanation": factor_exp,
        ...
        "global_factors": doc.global_factors or [],
        "constituent_signals": doc.constituent_signals or [],
        "plan_status": status,
        "pipeline_log": pipeline_log,
    }
```

**What it does:** Flattens `IndexResearchDoc` dataclass → JSON dict for React. Adds warnings if spot missing or attribution empty.

**Correctness:** UI never reads raw hub file directly during run — uses job artifact or `GET /index-prediction`.

---

## Artifact cross-check (live hub)

```
bottom_up=0.11  macro_delta=5.0  expected=8.45  debate_merged=True
```

**Finding N-13:** Headline ≠ `bottom_up + macro_delta` when debate merged. Verification must read `provenance` / `debate_merged`.

---

## Phase 0 deliverable

See [`00-verification-playbook.md`](00-verification-playbook.md).

**Commands run:** `audit_prediction_data.py --days 500` (exit 0); panel pytest 7 passed.

---

## Issues logged

| ID | Finding |
|----|---------|
| N-13 | Debate-adjusted headline breaks simple sum check |
| C6 | Documented in controls copy — first run needs refresh |
