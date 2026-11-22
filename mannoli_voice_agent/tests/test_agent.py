import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from build_agent import build, load_sources  # noqa: E402
from enquiries import EnquiryError, save_enquiry  # noqa: E402

GOOD = {"name": "Priya", "language": "ml", "enquiry_type": "bulk_or_gifting",
        "products": "Ganesha wall plaque", "quantity": "25", "city_or_pincode": "641002",
        "callback_time": "tomorrow evening", "whatsapp_consent": True}


def test_every_language_has_every_phrase():
    langs = json.loads((ROOT / "languages.json").read_text(encoding="utf-8"))
    assert set(langs["languages"]) == {"ta", "en", "hi", "ml", "kn", "te"}
    keys = set(langs["languages"]["en"])
    for code, phrases in langs["languages"].items():
        assert set(phrases) == keys, code
        assert all(v.strip() for v in phrases.values()), code


def test_tool_enum_matches_languages():
    _, _, langs, tools = load_sources()
    enum = set(tools[0]["parameters"]["properties"]["language"]["enum"])
    assert set(langs["languages"]) <= enum


def test_live_build_blocked_while_facts_pending():
    facts = (ROOT / "mannoli_facts.md").read_text(encoding="utf-8")
    if "TO CONFIRM" in facts:
        with pytest.raises(SystemExit):
            build(draft=False)


def test_draft_build_fills_placeholders():
    config = build(draft=True)
    assert "{{" not in config["system_prompt"]
    assert "മണ്ണൊലി" in config["system_prompt"] and "88076 24013" in config["system_prompt"]


def test_save_enquiry_writes_row(tmp_path):
    out = tmp_path / "e.csv"
    reply = save_enquiry(GOOD, out, caller_number="+91 98765 43210")
    assert reply["ok"] and reply["enquiry_id"].startswith("MN-")
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert rows[0]["caller_number"] == "+919876543210"
    assert rows[0]["whatsapp_consent"] == "yes" and rows[0]["status"] == "new"


@pytest.mark.parametrize("patch", [
    {"language": "fr"}, {"enquiry_type": "refund_now"}, {"whatsapp_consent": "yes"}, {"name": "  "},
])
def test_save_enquiry_rejects_bad_args(tmp_path, patch):
    with pytest.raises(EnquiryError):
        save_enquiry({**GOOD, **patch}, tmp_path / "e.csv")


def test_formula_injection_neutralised(tmp_path):
    out = tmp_path / "e.csv"
    save_enquiry({**GOOD, "notes": "=HYPERLINK(\"x\")"}, out)
    assert list(csv.DictReader(out.open(encoding="utf-8")))[0]["notes"].startswith("'=")


def test_webhook_requires_token(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import webhook_server
    monkeypatch.setattr(webhook_server, "CSV_PATH", tmp_path / "e.csv")
    monkeypatch.setenv("MANNOLI_WEBHOOK_TOKEN", "test-token-0123456789")
    client = TestClient(webhook_server.app)
    url = "/tools/save_enquiry"
    assert client.post(url, json=GOOD).status_code == 401
    assert client.post(url, json=GOOD, headers={"X-Mannoli-Token": "wrong"}).status_code == 401
    ok = client.post(url, json={"arguments": GOOD, "caller_number": "9876543210"},
                     headers={"X-Mannoli-Token": "test-token-0123456789"})
    assert ok.status_code == 200 and ok.json()["ok"]
    bad = client.post(url, json={**GOOD, "language": "xx"}, headers={"X-Mannoli-Token": "test-token-0123456789"})
    assert bad.status_code == 422
