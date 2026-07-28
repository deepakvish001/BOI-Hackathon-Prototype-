"""FastAPI application: scoring, investigation, containment and reporting.

Two audiences are served by the same process. ``/api/*`` is the machine
interface a core banking system would call inline; ``/`` is the investigator
dashboard. Keeping them together means the demo cannot drift from the API.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from bodhi import __version__
from bodhi.api.state import RuntimeState, get_state
from bodhi.compliance.reports import build_ctr_candidates, build_str, str_to_text
from bodhi.config import ALERT_THRESHOLD, ROOT, band_for
from bodhi.engine.agents import AGENTS
from bodhi.explain.gnn_explainer import explain_node
from bodhi.graph.rings import trace_flows
from bodhi.schemas import (
    ActionType,
    AlertStatus,
    HealthResponse,
    IngestBatchRequest,
    IngestBatchResponse,
    ScoreTransactionRequest,
)
from bodhi.shield.apk_triage import triage_apk
from bodhi.shield.ioc_bridge import ingest_iocs, iocs_from_report

DASHBOARD_DIR = ROOT / "dashboard"

app = FastAPI(
    title="BODHI MULE HUNTER AI",
    version=__version__,
    description="Real-time mule account and fraud transaction detection "
                "for the CyberShield / Bank of India problem statement.",
)


def _jsonable(value: Any) -> Any:
    """NumPy and pandas scalars are not JSON-serialisable by default."""
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


# --------------------------------------------------------------------------
# health & metadata
# --------------------------------------------------------------------------


@app.get("/api/health", response_model=HealthResponse)
def health(state: RuntimeState = Depends(get_state)) -> HealthResponse:
    open_alerts = len([a for a in state.casebook.alerts.values()
                       if a.status == AlertStatus.OPEN])
    return HealthResponse(
        status="ok" if state.ready else "degraded",
        version=__version__,
        models_loaded=state.engine.last_result is not None,
        accounts_in_graph=int(state.engine.graph.n_nodes) if state.engine.graph else 0,
        transactions_seen=int(len(state.transactions)) if state.ready else 0,
        open_alerts=open_alerts,
    )


@app.get("/api/agents")
def agents() -> dict[str, Any]:
    """The nine-layer agent topology, as executed."""
    return {"agents": [
        {"layer": a.layer, "name": a.name, "role": a.role,
         "consumes": list(a.consumes), "produces": list(a.produces)}
        for a in AGENTS
    ]}


@app.get("/api/overview")
def overview(state: RuntimeState = Depends(get_state)) -> dict[str, Any]:
    """Headline numbers for the dashboard banner."""
    result = state.engine.last_result
    if result is None:
        raise HTTPException(503, "engine has not scored yet")
    scores = result.scores
    bands = scores["band"].value_counts().to_dict()
    tx = state.transactions
    alerts = list(state.casebook.alerts.values())

    return _jsonable({
        "generated_at": time.time(),
        "bootstrap_mode": state.bootstrap_mode,
        "accounts": int(len(scores)),
        "transactions": int(len(tx)),
        "total_value": float(tx["amount"].sum()),
        "bands": {k: int(v) for k, v in bands.items()},
        "alerts_open": len([a for a in alerts if a.status == AlertStatus.OPEN]),
        "alerts_total": len(alerts),
        "amount_at_risk": float(sum(a.amount_at_risk for a in alerts)),
        "rings": len(result.rings),
        "largest_ring": max((r.size for r in result.rings), default=0),
        "active_restrictions": len(state.killswitch.active),
        "ncrp_tickets": int(len(state.ncrp.tickets)),
        "iocs": int(len(state.engine.iocs)) if state.engine.iocs is not None else 0,
        "audit_entries": len(state.audit),
        "audit_head": state.audit.head,
        "scoring_ms": result.elapsed_ms,
        "metrics": state.metrics.get("headline", {}) if state.metrics else {},
    })


@app.get("/api/metrics")
def metrics(state: RuntimeState = Depends(get_state)) -> dict[str, Any]:
    """Evaluation results produced by ``scripts/evaluate.py``."""
    return state.metrics or {"detail": "run `make evaluate` to populate metrics"}


# --------------------------------------------------------------------------
# alerts & investigation
# --------------------------------------------------------------------------


@app.get("/api/alerts")
def list_alerts(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    min_score: float = Query(0.0, ge=0, le=100),
    state: RuntimeState = Depends(get_state),
) -> dict[str, Any]:
    st = AlertStatus(status) if status else None
    items = state.casebook.queue(status=st, limit=limit, min_score=min_score)
    return _jsonable({
        "count": len(items),
        "stats": state.casebook.stats(),
        "alerts": [a.model_dump(mode="json") for a in items],
    })


@app.get("/api/alerts/{alert_id}")
def get_alert(alert_id: str, state: RuntimeState = Depends(get_state)) -> dict[str, Any]:
    alert = state.casebook.get(alert_id)
    if alert is None:
        raise HTTPException(404, f"unknown alert {alert_id}")
    detail = state.engine.explain_account(alert.account_id)
    return _jsonable({
        "alert": alert.model_dump(mode="json"),
        "detail": detail,
        "notes": state.casebook.notes.get(alert_id, []),
        "restriction": state.killswitch.status(alert.account_id),
    })


@app.post("/api/alerts/{alert_id}/resolve")
def resolve_alert(
    alert_id: str,
    status: str = Query(..., description="CONFIRMED_FRAUD | FALSE_POSITIVE | CLOSED"),
    investigator: str = Query("analyst@bank"),
    note: str = Query(""),
    state: RuntimeState = Depends(get_state),
) -> dict[str, Any]:
    try:
        target = AlertStatus(status)
    except ValueError:
        raise HTTPException(400, f"invalid status {status!r}")
    alert = state.casebook.resolve(alert_id, target, investigator, note)
    if alert is None:
        raise HTTPException(404, f"unknown alert {alert_id}")
    return _jsonable({"alert": alert.model_dump(mode="json"),
                      "stats": state.casebook.stats()})


@app.get("/api/accounts/{account_id}")
def account_detail(account_id: str,
                   state: RuntimeState = Depends(get_state)) -> dict[str, Any]:
    """Full account 360: score, layers, SHAP, evidence, ring, narrative."""
    try:
        detail = state.engine.explain_account(account_id)
    except KeyError:
        raise HTTPException(404, f"unknown account {account_id}")
    row = state.account_row(account_id)
    alerts = state.casebook.for_account(account_id)
    tickets = state.ncrp.tickets
    linked = tickets[tickets["beneficiary_account"] == account_id] \
        if len(tickets) else pd.DataFrame()
    return _jsonable({
        **detail,
        "profile": row,
        "alerts": [a.model_dump(mode="json") for a in alerts],
        "restriction": state.killswitch.status(account_id),
        "government_tickets": json.loads(linked.head(20).to_json(orient="records"))
        if len(linked) else [],
    })


@app.get("/api/accounts/{account_id}/graph")
def account_graph(
    account_id: str,
    hops: int = Query(1, ge=1, le=2),
    max_nodes: int = Query(60, ge=5, le=250),
    state: RuntimeState = Depends(get_state),
) -> dict[str, Any]:
    """Ego network around an account, ready for force-directed rendering."""
    graph = state.engine.graph
    if graph is None:
        raise HTTPException(503, "graph not built")
    centre = graph.code(account_id)
    if centre < 0:
        raise HTTPException(404, f"unknown account {account_id}")

    scores = state.engine.last_result.scores
    nodes: dict[int, dict[str, Any]] = {}
    frontier = [centre]
    seen = {centre}
    for depth in range(hops + 1):
        nxt: list[int] = []
        for v in frontier:
            nodes[v] = {"depth": depth}
            if depth == hops:
                continue
            nbr, w, et = graph.neighbours(v)
            if nbr.size > max_nodes:
                nbr, w, et = (nbr[np.argsort(-w)[:max_nodes]],
                              np.sort(w)[::-1][:max_nodes],
                              et[np.argsort(-w)[:max_nodes]])
            for t in nbr:
                t = int(t)
                if t not in seen and len(seen) < max_nodes:
                    seen.add(t)
                    nxt.append(t)
        frontier = nxt

    ids = {v: str(graph.index[v]) for v in nodes}
    accounts = state.accounts.set_index("account_id")
    out_nodes = []
    for v, meta in nodes.items():
        aid = ids[v]
        score = float(scores["risk_score"].get(aid, 0.0))
        out_nodes.append({
            "id": aid,
            "depth": meta["depth"],
            "risk": round(score, 2),
            "band": band_for(score),
            "is_centre": v == centre,
            "kyc": str(accounts["kyc_level"].get(aid, "")),
            "vintage_days": int(accounts["account_vintage_days"].get(aid, 0)),
        })

    node_set = set(nodes)
    edges = []
    money = graph
    mask = np.isin(money.src, list(node_set)) & np.isin(money.dst, list(node_set))
    idx = np.flatnonzero(mask)
    if idx.size > 400:
        idx = idx[np.argsort(-money.amount[idx])[:400]]
    agg: dict[tuple[str, str], dict[str, float]] = {}
    for i in idx:
        key = (ids[int(money.src[i])], ids[int(money.dst[i])])
        e = agg.setdefault(key, {"amount": 0.0, "count": 0.0})
        e["amount"] += float(money.amount[i])
        e["count"] += 1
    for (s, t), e in agg.items():
        edges.append({"source": s, "target": t, "type": "MONEY",
                      "amount": round(e["amount"], 2), "count": int(e["count"])})

    # Device co-location edges make account farms visible at a glance.
    from bodhi.graph.builder import EDGE_TYPES
    for v in node_set:
        nbr, w, et = graph.neighbours(v)
        for t, kind in zip(nbr, et):
            t = int(t)
            if t in node_set and v < t and int(kind) == EDGE_TYPES["DEVICE"]:
                edges.append({"source": ids[v], "target": ids[t],
                              "type": "DEVICE", "amount": 0.0, "count": 1})

    return _jsonable({"centre": account_id, "nodes": out_nodes, "edges": edges})


@app.get("/api/accounts/{account_id}/flows")
def account_flows(
    account_id: str,
    hops: int = Query(4, ge=1, le=6),
    window_hours: float = Query(48.0, gt=0, le=720),
    state: RuntimeState = Depends(get_state),
) -> dict[str, Any]:
    """Time-respecting money trails leaving this account."""
    graph = state.engine.graph
    if graph is None or graph.code(account_id) < 0:
        raise HTTPException(404, f"unknown account {account_id}")
    paths = trace_flows(graph, account_id, max_hops=hops, window_hours=window_hours)
    scores = state.engine.last_result.scores
    for p in paths:
        p["risk_along_path"] = [round(float(scores["risk_score"].get(a, 0.0)), 1)
                                for a in p["path"]]
    return _jsonable({"account_id": account_id, "paths": paths})


@app.get("/api/accounts/{account_id}/gnn-explain")
def gnn_explain(account_id: str,
                state: RuntimeState = Depends(get_state)) -> dict[str, Any]:
    """Which *relationships* drove the graph score (GNNExplainer edge mask)."""
    engine = state.engine
    if engine.graph is None or engine.features is None:
        raise HTTPException(503, "graph or features unavailable")
    if engine.graph.code(account_id) < 0:
        raise HTTPException(404, f"unknown account {account_id}")
    result = explain_node(engine.sage, engine.graph,
                          engine.features.to_numpy(dtype=float), account_id)
    return _jsonable(result)


@app.get("/api/rings")
def rings(limit: int = Query(20, ge=1, le=100),
          state: RuntimeState = Depends(get_state)) -> dict[str, Any]:
    result = state.engine.last_result
    if result is None:
        raise HTTPException(503, "engine has not scored yet")
    return _jsonable({"count": len(result.rings),
                      "rings": [r.to_dict() for r in result.rings[:limit]]})


# --------------------------------------------------------------------------
# real-time scoring & ingestion
# --------------------------------------------------------------------------


@app.post("/api/score/transaction")
def score_transaction(req: ScoreTransactionRequest,
                      state: RuntimeState = Depends(get_state)) -> dict[str, Any]:
    """Inline decision for one in-flight payment."""
    txn = req.transaction.model_dump()
    txn["ts"] = req.transaction.timestamp.timestamp()
    decision = state.engine.score_transaction(txn)
    state.audit.append("TXN_AGENT", "TXN_DECISION", {
        "txn_id": decision.txn_id, "decision": decision.decision,
        "risk_score": decision.risk_score,
    })
    return _jsonable(decision.model_dump(mode="json"))


@app.post("/api/ingest", response_model=IngestBatchResponse)
def ingest(req: IngestBatchRequest,
           rescore: bool = Query(False),
           state: RuntimeState = Depends(get_state)) -> IngestBatchResponse:
    """Push new events, tickets, IoCs or directives into the running engine."""
    t0 = time.perf_counter()
    with state.lock:
        added_tx = 0
        if req.transactions:
            rows = []
            for t in req.transactions:
                d = t.model_dump()
                d["ts"] = t.timestamp.timestamp()
                d["channel"] = t.channel.value
                d["txn_type"] = t.txn_type.value
                rows.append(d)
            new = pd.DataFrame(rows)
            state.engine.transactions = pd.concat(
                [state.engine.transactions, new], ignore_index=True
            ).sort_values("ts").reset_index(drop=True)
            added_tx = len(rows)

        added_alerts = state.ncrp.ingest(req.fraud_alerts) if req.fraud_alerts else 0
        if added_alerts:
            state.engine.fraud_alerts = state.ncrp.tickets

        added_iocs = 0
        if req.iocs:
            state.engine.iocs = ingest_iocs(list(req.iocs), state.engine.iocs)
            added_iocs = len(req.iocs)

        added_reg = state.regulatory.ingest(req.regulatory) if req.regulatory else 0
        if added_reg:
            state.engine.regulatory = state.regulatory.items

        new_alerts = []
        if rescore:
            state.engine.score_all()
            fresh = state.engine.generate_alerts(threshold=ALERT_THRESHOLD, limit=50)
            added = state.casebook.add(fresh)
            new_alerts = fresh[:added] if added else []

        state.audit.append("INGEST_AGENT", "BATCH_INGEST", {
            "transactions": added_tx, "tickets": added_alerts,
            "iocs": added_iocs, "regulatory": added_reg, "rescored": rescore,
        })

    return IngestBatchResponse(
        accepted_transactions=added_tx,
        accepted_alerts=added_alerts,
        accepted_iocs=added_iocs,
        accepted_regulatory=added_reg,
        new_alerts=new_alerts,
        processing_ms=round((time.perf_counter() - t0) * 1000, 2),
    )


# --------------------------------------------------------------------------
# BODHI SHIELD - APK analysis
# --------------------------------------------------------------------------


@app.post("/api/shield/analyze")
async def shield_analyze(
    file: UploadFile = File(...),
    inject: bool = Query(True, description="Push extracted IoCs into the graph"),
    state: RuntimeState = Depends(get_state),
) -> dict[str, Any]:
    """Static triage of an uploaded APK, with hand-off to the fraud graph."""
    payload = await file.read()
    if len(payload) > 200 * 1024 * 1024:
        raise HTTPException(413, "file exceeds the 200 MB triage limit")
    with tempfile.NamedTemporaryFile(suffix=".apk", delete=False) as fh:
        fh.write(payload)
        tmp = Path(fh.name)
    try:
        report = triage_apk(tmp)
        iocs = iocs_from_report(report)
        matched: list[dict[str, Any]] = []
        if inject and iocs:
            state.engine.iocs = ingest_iocs(iocs, state.engine.iocs)
            accounts = state.accounts
            values = {i.value for i in iocs}
            hit = accounts[accounts["vpa"].isin(values)
                           | accounts["account_id"].isin(values)]
            scores = state.engine.last_result.scores if state.engine.last_result else None
            for _, r in hit.iterrows():
                aid = str(r["account_id"])
                matched.append({
                    "account_id": aid,
                    "vpa": str(r["vpa"]),
                    "current_risk": (round(float(scores["risk_score"].get(aid, 0.0)), 2)
                                     if scores is not None else None),
                })
        state.audit.append("SHIELD_AGENT", "APK_TRIAGE", {
            "file": file.filename, "sha256": report.sha256,
            "risk_band": report.risk_band, "iocs": len(iocs),
            "matched_accounts": len(matched),
        })
        return _jsonable({
            "report": report.to_dict(),
            "iocs": [i.model_dump(mode="json") for i in iocs],
            "matched_accounts": matched,
            "handoff": {
                "injected": bool(inject and iocs),
                "graph_nodes_primed": len(matched),
                "note": "Extracted beneficiary identifiers are now weighted "
                        "nodes in the Mule Hunter graph.",
            },
        })
    finally:
        tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# containment & compliance
# --------------------------------------------------------------------------


@app.post("/api/actions/killswitch")
def killswitch(
    account_id: str = Query(...),
    action: str = Query("FREEZE_DEBIT"),
    actor: str = Query("analyst@bank"),
    state: RuntimeState = Depends(get_state),
) -> dict[str, Any]:
    """Ask the kill-switch to apply a containment action."""
    result = state.engine.last_result
    if result is None or account_id not in result.scores.index:
        raise HTTPException(404, f"unknown account {account_id}")
    try:
        proposed = ActionType(action)
    except ValueError:
        raise HTTPException(400, f"invalid action {action!r}")
    row = result.scores.loc[account_id]
    decision = state.killswitch.evaluate(
        account_id=account_id,
        risk_score=float(row["risk_score"]),
        proposed=proposed,
        layer_scores={
            "xgb": float(row["score_xgb"]), "graph": float(row["score_graph"]),
            "temporal": float(row["score_temporal"]),
            "intel": float(row["score_intel"]),
        },
        amount_at_risk=state.engine._exposure(account_id),
        actor=actor,
    )
    return _jsonable({"decision": decision.to_dict(),
                      "summary": state.killswitch.summary()})


@app.post("/api/actions/revert")
def revert(account_id: str = Query(...), actor: str = Query("analyst@bank"),
           reason: str = Query("reviewed and cleared"),
           state: RuntimeState = Depends(get_state)) -> dict[str, Any]:
    ok = state.killswitch.revert(account_id, actor, reason)
    if not ok:
        raise HTTPException(404, f"no active restriction on {account_id}")
    return {"reverted": True, "account_id": account_id}


@app.get("/api/reports/str/{alert_id}")
def draft_str(alert_id: str, as_text: bool = Query(False),
              state: RuntimeState = Depends(get_state)):
    """Draft a Suspicious Transaction Report for an alert."""
    alert = state.casebook.get(alert_id)
    if alert is None:
        raise HTTPException(404, f"unknown alert {alert_id}")
    result = state.engine.last_result
    members: list[str] = []
    if result:
        for r in result.rings:
            if alert.account_id in r.members:
                members = [m for m in r.members if m != alert.account_id]
                break
    report = build_str(alert, state.account_row(alert.account_id),
                       state.transactions, members)
    state.audit.append("COMPLIANCE_AGENT", "STR_DRAFTED",
                       {"alert_id": alert_id, "reference": report["reference"]})
    if as_text:
        return PlainTextResponse(str_to_text(report))
    return _jsonable(report)


@app.get("/api/reports/ctr")
def ctr(state: RuntimeState = Depends(get_state)) -> dict[str, Any]:
    """Cash transactions breaching the aggregate reporting threshold."""
    df = build_ctr_candidates(state.transactions)
    return _jsonable({"count": int(len(df)),
                      "candidates": json.loads(df.head(100).to_json(orient="records"))})


@app.get("/api/audit")
def audit(limit: int = Query(80, ge=1, le=1000),
          state: RuntimeState = Depends(get_state)) -> dict[str, Any]:
    ok, bad = state.audit.verify()
    return _jsonable({
        "entries": len(state.audit),
        "chain_valid": ok,
        "first_invalid_seq": bad,
        "head": state.audit.head,
        "tail": state.audit.tail(limit),
    })


# --------------------------------------------------------------------------
# dashboard
# --------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    index = DASHBOARD_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>BODHI MULE HUNTER AI</h1>"
                            "<p>Dashboard assets not found. API is at /docs.</p>")
    return HTMLResponse(index.read_text(encoding="utf-8"))


@app.get("/favicon.ico", response_model=None)
def favicon() -> FileResponse | PlainTextResponse:
    path = DASHBOARD_DIR / "favicon.svg"
    if path.exists():
        return FileResponse(path, media_type="image/svg+xml")
    return PlainTextResponse("", status_code=204)


if DASHBOARD_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")


__all__ = ["app"]
