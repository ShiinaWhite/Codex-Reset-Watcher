"""Offline forensic replay, not a proposed eligibility implementation.

Run from repository root: PYTHONPATH=test_env python evidence/v0.1.12/replay.py
The time/receipt/baseline gates are deliberately not bypassed in production;
this report compares extraction only, at the event's original occurrence.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from codex_reset_watcher.plugin import signals_from_feed

HERE = Path(__file__).resolve().parent
feed = json.loads((HERE / "feed.json").read_text(encoding="utf-8"))
notification = json.loads((HERE / "push_notification.json").read_text(encoding="utf-8"))
selected = notification["alert"]
old_ids = {s.event_id for s in signals_from_feed(feed) if s.lane == "banked"}
tg_sent = {
    "2097752790177370535", "2096035437299237298", "2095979536043401428",
    "2095651088502591861", "2090947196107764189", "2090766694897619318",
}
rows = []
for event in feed["events"]:
    if "banked_state" not in event:
        continue
    event_id = event["id"]
    rows.append({
        "id": event_id,
        "banked_state": event["banked_state"],
        "reset_kind": event.get("reset_kind"),
        "tg_delivery_evidence": "sent" if event_id in tg_sent else "unknown",
        "old_extraction": "eligible" if event_id in old_ids else "silent",
        "new_production_behavior": "not_implemented_contract_gap",
        "present_in_current_push_record": selected.get("kind") == "banked" and selected.get("id") == event_id,
    })
assert "2097752790177370535" not in old_ids
assert selected["id"] == "2097752790177370535" and selected["kind"] == "banked"
assert next(e for e in feed["events"] if e["id"] == selected["id"])["banked_state"] == "unknown"
print(json.dumps(rows, ensure_ascii=False, indent=2))
