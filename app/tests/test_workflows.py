"""Contracts the shipped workflow JSON has to keep. Pure file checks, no n8n needed."""
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
WF_DIR = ROOT / "workflows"
IDS = json.loads((ROOT / "deploy" / "n8n" / "ids.json").read_text(encoding="utf-8"))
FILES = sorted(WF_DIR.glob("*.json"))


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def nodes(wf):
    return [n for n in wf["nodes"] if n["type"] != "n8n-nodes-base.stickyNote"]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_workflow_carries_its_pinned_id_and_tags(path):
    wf = load(path)
    assert IDS["workflows"].get(wf["name"]) == wf.get("id"), "id must match deploy/n8n/ids.json"
    assert wf.get("tags"), "every workflow is tagged"
    assert all(isinstance(t, str) for t in wf["tags"]), "tags are plain names in the repo"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_import_form_expands_tags_to_objects(path):
    # import:workflow drops a tag with no .name and then writes a null tagId, so bootstrap expands.
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import bootstrap
    tags = bootstrap.for_import(load(path))["tags"]
    assert tags and all(isinstance(t, dict) and t.get("name") for t in tags)


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_node_ids_are_unique(path):
    # n8n refuses the whole import on a duplicate, stickies included.
    ids = [n["id"] for n in load(path)["nodes"]]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"{path.name}: duplicate node id(s) {sorted(dupes)}"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_credentials_bind_by_id_and_name(path):
    for n in nodes(load(path)):
        for ctype, ref in (n.get("credentials") or {}).items():
            known = IDS["credentials"].get(ref.get("name"))
            assert known, f"{path.name}:{n['name']} uses unknown credential {ref.get('name')!r}"
            assert ref.get("id") == known["id"], f"{path.name}:{n['name']} credential id not pinned"
            assert known["type"] == ctype


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_sub_workflow_references_resolve(path):
    for n in nodes(load(path)):
        ref = (n.get("parameters") or {}).get("workflowId")
        if isinstance(ref, dict) and ref.get("__workflowName"):
            assert ref.get("value") == IDS["workflows"][ref["__workflowName"]]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_hostname_or_mail_node_is_baked_in(path):
    raw = path.read_text(encoding="utf-8")
    for bad in ("microsoftOutlook", "svc.cluster.local", "10.10.0.", "localhost:5679"):
        assert bad not in raw, f"{path.name} still contains {bad}"


def test_daily_sends_the_brief_to_telegram_within_the_message_cap():
    wf = load(WF_DIR / "daily.json")
    by = {n["name"]: n for n in nodes(wf)}
    assert by["Telegram brief"]["type"] == "n8n-nodes-base.telegram"
    assert by["Telegram document"]["parameters"]["operation"] == "sendDocument"
    code = by["Build brief"]["parameters"]["jsCode"]
    assert "telegram_text" in code
    # Telegram rejects a message over 4096 characters, so the short form is trimmed below it.
    assert ".slice(0, 3900)" in code
    assert by["Telegram enabled?"]["parameters"]["conditions"]["conditions"][0]["operator"]["operation"] == "notEmpty"


def test_app_callbacks_come_from_config():
    for name in ("daily.json", "entry-signals.json"):
        raw = (WF_DIR / name).read_text(encoding="utf-8")
        assert "app_base_url" in raw, f"{name} should read intel.config.app_base_url"


def test_every_workflow_has_a_zone_spec():
    for path in FILES:
        spec = WF_DIR / "zones" / path.name
        assert spec.is_file(), f"{path.name} has no zone spec; the deploy gate will refuse it"
        listed = {n for z in json.loads(spec.read_text(encoding="utf-8"))["zones"] for n in z["nodes"]}
        assert {n["name"] for n in nodes(load(path))} == listed, f"{path.name}: nodes and zones disagree"


def test_webhook_workflows_are_activated():
    # A production webhook only answers while its workflow is active.
    active = set(IDS["activate"]) | set(IDS["activate_intraday"])
    for path in FILES:
        wf = load(path)
        if any(n["type"] == "n8n-nodes-base.webhook" for n in nodes(wf)):
            assert wf["name"] in active, f"{wf['name']} serves a webhook but is never activated"
