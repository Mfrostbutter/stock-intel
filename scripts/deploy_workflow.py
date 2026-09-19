#!/usr/bin/env python3
"""Deploy a Stock Intel workflow JSON to n8n (create or update in place).

Reads: workflows/<name>.json, the credential ledger (credential name -> id) and the
       workflow ledger (workflow name -> id), N8N_BASE_URL and N8N_API_KEY from .env.
Credentials in the JSON are written by NAME only; this script fills the id
from the ledger, or strips the block when the id is unknown.
Usage: python scripts/deploy_workflow.py workflows/collect-prices.json [--activate]
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

import secrets_env

# Ledger paths are overridable so one checkout can drive more than one instance.
N8N = secrets_env.get("N8N_BASE_URL", "http://localhost:5678").rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))
CRED_LEDGER = os.path.join(HERE, os.environ.get("SI_N8N_CRED_LEDGER", "n8n_creds.json"))
WF_LEDGER = os.path.join(HERE, os.environ.get("SI_N8N_WF_LEDGER", "n8n_workflows.json"))
SETTINGS_KEYS = ("executionOrder", "timezone", "saveDataErrorExecution",
                 "saveDataSuccessExecution", "saveManualExecutions",
                 "saveExecutionProgress", "errorWorkflow", "executionTimeout")


def api_key():
    # Created once in the n8n UI: Settings -> n8n API -> Create an API key.
    return secrets_env.require(os.environ.get("SI_N8N_KEY_NAME", "N8N_API_KEY"))


def n8n(method, path, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(N8N + path, data=data, method=method)
    req.add_header("X-N8N-API-KEY", key)
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            t = r.read().decode()
            return r.status, (json.loads(t) if t else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:500]}


def load(path):
    return json.load(open(path)) if os.path.exists(path) else {}


def layout_gate(src):
    """HARD gate: refuse to deploy an undocumented canvas. Runs scripts/canvas/layout_check.py;
    no `RESULT: ok`, no deploy. Override the checker path with SI_LAYOUT_CHECK."""
    checker = os.environ.get("SI_LAYOUT_CHECK") or os.path.join(HERE, "canvas", "layout_check.py")
    if not os.path.exists(checker):
        raise SystemExit(f"canvas gate: layout_check.py not found ({checker}); set SI_LAYOUT_CHECK")
    r = subprocess.run([sys.executable, checker, src], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    print(r.stdout.strip())
    if r.returncode != 0 or "RESULT: ok" not in r.stdout:
        raise SystemExit("canvas gate FAILED: document the canvas (zone spec in workflows/zones/ + "
                         "scripts/canvas/zone_layout.py) until layout_check prints RESULT: ok.")


def ensure_tags(names, key):
    """Resolve tag names to ids on the target instance, creating any that are missing."""
    code, resp = n8n("GET", "/api/v1/tags?limit=250", key)
    existing = {t["name"]: t["id"] for t in resp.get("data", [])} if code == 200 else {}
    ids = []
    for name in names:
        tid = existing.get(name)
        if not tid:
            c, r = n8n("POST", "/api/v1/tags", key, {"name": name})
            if c not in (200, 201):
                raise SystemExit(f"tag create FAILED for '{name}' HTTP {c}: {r.get('error')}")
            tid = r["id"]
            existing[name] = tid
        ids.append(tid)
    return ids


def assign_tags(wid, names, key):
    """Replace the workflow's tags with the named set (public API PUT /workflows/{id}/tags)."""
    ids = ensure_tags(names, key)
    c, r = n8n("PUT", f"/api/v1/workflows/{wid}/tags", key, [{"id": i} for i in ids])
    if c not in (200, 201):
        raise SystemExit(f"tag assign FAILED HTTP {c}: {r if isinstance(r, dict) else ''}")


def wire_credentials(wf, creds, wf_ids):
    """Ledger id wins; otherwise the pinned id shipped in the JSON stands."""
    for node in wf["nodes"]:
        block = node.get("credentials")
        if block:
            for ctype, ref in list(block.items()):
                cid = creds.get(ref.get("name", "")) or ref.get("id")
                if cid:
                    block[ctype] = {"id": cid, "name": ref["name"]}
                else:
                    print(f"  no credential id for '{ref.get('name')}' on node '{node['name']}', block stripped")
                    del block[ctype]
            if not block:
                del node["credentials"]
        # Execute Sub-workflow nodes carry the pinned id plus __workflowName for this path.
        p = node.get("parameters", {})
        ref = p.get("workflowId", {})
        if isinstance(ref, dict) and ref.get("__workflowName"):
            sub = ref["__workflowName"]
            wid = wf_ids.get(sub) or ref.get("value")
            if not wid:
                raise SystemExit(f"sub-workflow '{sub}' not deployed yet")
            p["workflowId"] = {"__rl": True, "mode": "id", "value": wid}
    return wf


def main():
    src = sys.argv[1]
    activate = "--activate" in sys.argv
    wf = json.load(open(src))

    # HARD gates (fail before touching the server): every workflow is tagged (SOP,
    # so instances stay sortable) and its canvas passes the n8n-canvas-docs check.
    tags = wf.get("tags") or []
    if not tags:
        raise SystemExit('tag gate: workflow has no tags. Add "tags": [...] to the JSON '
                         "(SOP: every workflow is tagged).")
    layout_gate(src)

    creds, wf_ids = load(CRED_LEDGER), load(WF_LEDGER)
    wf = wire_credentials(wf, creds, wf_ids)
    body = {
        "name": wf["name"],
        "nodes": wf["nodes"],
        "connections": wf["connections"],
        "settings": {k: v for k, v in wf.get("settings", {}).items() if k in SETTINGS_KEYS},
    }
    key = api_key()
    # Ledger first (a developer instance), then the pinned id the bootstrap import used.
    wid = wf_ids.get(wf["name"]) or wf.get("id")

    # Activate BEFORE the PUT. POST /activate republishes whatever version was
    # already active, which silently discards a PUT made just before it: on
    # 2026-09-09 that dropped Collect-Prices out of Backfill with no error and
    # the post-PUT read-back still looked clean because it returned the draft.
    if activate and wid:
        code, _ = n8n("POST", f"/api/v1/workflows/{wid}/activate", key)
        print(f"pre-activate -> HTTP {code}")

    code, resp, verb = 404, {}, "updated"
    if wid:
        code, resp = n8n("PUT", f"/api/v1/workflows/{wid}", key, body)
    if code == 404:   # pinned id not on this instance yet
        code, resp = n8n("POST", "/api/v1/workflows", key, body)
        verb = "created"
    if code not in (200, 201):
        raise SystemExit(f"{verb} FAILED HTTP {code}: {resp.get('error')}")
    wid = resp["id"]
    wf_ids[wf["name"]] = wid
    json.dump(wf_ids, open(WF_LEDGER, "w"), indent=2)
    print(f"{verb} {wf['name']} -> {wid}")

    if activate:
        code, resp = n8n("POST", f"/api/v1/workflows/{wid}/activate", key)
        print(f"activate -> HTTP {code}")

    # Read back: a node silently dropped by the server is worse than a failed deploy.
    code, live = n8n("GET", f"/api/v1/workflows/{wid}", key)
    want = {n["name"] for n in body["nodes"]}
    got = {n["name"] for n in live.get("nodes", [])} if code == 200 else set()
    if want != got:
        raise SystemExit(f"VERIFY FAILED: missing on server {sorted(want - got)}, extra {sorted(got - want)}")
    print(f"verified {len(got)} nodes on server")

    # A draft that never got published is a deploy that never landed.
    if live.get("versionId") != live.get("activeVersionId"):
        raise SystemExit("VERIFY FAILED: an unpublished draft is live "
                         f"(versionId {live.get('versionId')} != activeVersionId {live.get('activeVersionId')}). "
                         "Re-run the deploy, or press Publish in the editor.")
    print("published (versionId == activeVersionId)")

    assign_tags(wid, tags, key)
    print(f"tagged -> {tags}")


if __name__ == "__main__":
    main()
