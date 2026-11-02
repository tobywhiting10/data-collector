import os, tempfile
d = tempfile.mkdtemp()
os.environ.update(DB_PATH=f"{d}/t.db", SONGS_FILE=os.path.join(os.path.dirname(__file__), "..", "songs.yaml"),
                  API_KEY="test-key-1234567890", CHARITY_SHARE="0.90", FEE_PERCENT="1.69")
from fastapi.testclient import TestClient
from app.main import app

H = {"X-API-Key": "test-key-1234567890"}

def test_all():
    with TestClient(app) as c:
        assert c.get("/health").json() == {"ok": True}
        # writes need the key
        assert c.post("/api/v1/events/payment", json={"id": "abc-1", "amount": "8.00"}).status_code == 401
        # payments: idempotent, and the maths
        assert c.post("/api/v1/events/payment", headers=H, json={"id": "abc-1", "amount": "8.00"}).json()["stored"]
        assert c.post("/api/v1/events/payment", headers=H, json={"id": "abc-1", "amount": "8.00"}).json()["duplicate"]
        assert c.post("/api/v1/events/payment", headers=H, json={"id": "abc-2", "amount": 100}).json()["stored"]
        assert c.post("/api/v1/events/payment", headers=H, json={"id": "abc-3", "amount": 5000}).status_code == 422
        # plays: only catalogue songs, idempotent
        f = "Christmas Wrapping - The Waitresses.fseq"
        p = {"event_id": "p1-111", "file": f, "started_at": "2026-11-01T10:00:00+00:00", "seconds": 300, "source": "visitor"}
        assert c.post("/api/v1/events/play", headers=H, json=p).json()["stored"]
        assert c.post("/api/v1/events/play", headers=H, json=p).json()["duplicate"]
        p2 = {**p, "event_id": "p2-222", "file": "fade.fseq"}
        assert c.post("/api/v1/events/play", headers=H, json=p2).json()["stored"] is False
        p3 = {**p, "event_id": "p3-333", "seconds": 61, "started_at": "2026-11-01T11:00:00+00:00"}
        c.post("/api/v1/events/play", headers=H, json=p3)
        assert c.post("/api/v1/external/cruk_page", headers=H, json={"total": "250.50"}).json()["ok"]
        s = c.get("/api/v1/stats").json()
        card = s["donations"]["card"]
        # gross 108.00; fees 8.00*1.69% = 14p (0.1352) + 100*1.69% = 169p -> 183p; net 10617p; 90% = 9555p (9555.3)
        assert card["gross"] == 108.0 and card["estimated_fees"] == 1.83 and card["net_after_fees"] == 106.17
        assert card["to_charity"] == 95.55 and card["to_project"] == 10.62, card
        assert s["donations"]["total_raised_for_charity"] == 250.50   # CRUK page only (card money is transferred by hand)
        assert "buy_me_a_coffee" not in s["donations"]
        assert s["listening"]["total_seconds"] == 361 and s["listening"]["human"] == "0h 6m"
        assert s["top_songs"][0]["file"] == f and s["top_songs"][0]["plays"] == 2
        assert len(s["plays_by_day"]) == 30
        assert c.get("/api/v1/songs").json()["files"].count(f) == 1
        import json; print(json.dumps(s, indent=1)[:2500])


def test_cruk_parse():
    from app.main import parse_cruk
    assert parse_cruk('<div>Total raised</div><span>&pound;12.50</span> <b>+ £3.12 Gift Aid</b>') == (1250, 312)
    assert parse_cruk('<p>Total raised£1,234.00</p>') == (123400, 0)
    assert parse_cruk('<p>nothing here</p>') is None


def test_buttons_yaml():
    import yaml
    with TestClient(app) as c:
        t = c.get("/api/v1/songs/buttons.yaml").text
    blocks = [yaml.safe_load(b.split("\n", 1)[1]) for b in t.split("# ---- ")[1:]]
    assert blocks and blocks[0][0]["type"] == "heading" and blocks[0][1]["type"] == "custom:button-card"


def test_dash_and_summary():
    with TestClient(app) as c:
        for i, a in enumerate(["8.00", "5.00", "10.00"]):
            c.post("/api/v1/events/payment", headers=H, json={"id": f"dash-pay-{i}", "amount": a})
        assert c.post("/dash/api/cruk", json={"total": "100"}).json()["ok"]
        assert c.post("/dash/api/transfers", json={"amount": "20.00", "note": "wk1"}).json()["ok"]
        assert c.post("/dash/api/bmc", json={"amount": "7.50", "date": "2020-01-01"}).json()["ok"]   # old: not in 2 months
        assert c.post("/dash/api/bmc", json={"amount": "3.00"}).json()["ok"]
        assert c.post("/dash/api/transfers", json={"amount": "-1"}).status_code == 422
        assert c.post("/dash/api/bmc", json={"amount": "1", "date": "bad"}).status_code == 422
        m = c.get("/api/v1/summary").json()
        assert m["cruk_only"] == 100.0 and m["card_total"] >= 23.0 and m["moved_to_cruk"] == 20.0
        assert m["total"] == round(100.0 + m["card_charity"] - 20.0, 2) and m["goal"] == 1000.0
        assert m["bmc_last_2_months"] == 3.0 and m["bmc_total"] == 10.5
        assert m["project_total"] == round(m["card_project"] + m["bmc_total_net"], 2) and m["bmc_total_net"] < 10.5
        st = c.get("/dash/api/state").json()
        assert len(st["transfers"]) == 1 and len(st["bmc"]) == 2
        assert "<title>" in c.get("/dash").text
        c.delete(f"/dash/api/transfers/{st['transfers'][0]['id']}")
        assert c.get("/api/v1/summary").json()["moved_to_cruk"] == 0.0


def test_bmc_webhook():
    import hmac as _h, hashlib, json as _j
    import app.main as m
    m.BMC_WEBHOOK_SECRET = "whsec_test"
    def send(c, ev, sig=True):
        raw = _j.dumps(ev).encode()
        sg = _h.new(b"whsec_test", raw, hashlib.sha256).hexdigest() if sig else "bad"
        return c.post("/api/v1/webhooks/bmc", content=raw, headers={"x-signature-sha256": sg, "Content-Type": "application/json"})
    with TestClient(app) as c:
        before = c.get("/api/v1/summary").json()["bmc_total"]
        ev = {"event_id": 1, "type": "donation.created", "live_mode": True, "created": 1719825600, "attempt": 1,
              "data": {"id": 777001, "amount": 15.0, "currency": "GBP", "created_at": 1719825600}}
        assert send(c, ev, sig=False).status_code == 401
        assert send(c, ev).json()["stored"] is True
        assert send(c, ev).json()["stored"] is False                      # retry is idempotent
        assert c.get("/api/v1/summary").json()["bmc_total"] == round(before + 15.0, 2)
        assert send(c, {**ev, "data": {**ev["data"], "id": 777002, "currency": "USD"}}).json()["ignored"] == "currency"
        assert "test event" in send(c, {**ev, "live_mode": False}).json()["ignored"]
        assert send(c, {**ev, "type": "donation.refunded"}).json()["removed"] == 1
        assert c.get("/api/v1/summary").json()["bmc_total"] == before
        assert send(c, {**ev, "type": "recurring_donation.started"}).json()["ignored"] == "recurring_donation.started"


def test_settings_change_recalculates():
    import app.main as m
    async def fake(client):
        return True
    orig = m.collect_cruk; m.collect_cruk = fake
    try:
        with TestClient(app) as c:
            c.post("/api/v1/events/payment", headers=H, json={"id": "set-pay-1", "amount": "100.00"})
            a = c.get("/api/v1/summary").json()
            r = c.post("/dash/api/settings", json={"cruk_goal": 500, "charity_percent": 50, "card_fee_percent": 0, "card_fee_fixed_pence": 0,
                                                    "bmc_fee_percent": 0, "bmc_fee_fixed_pence": 0}).json()
            assert r["ok"] and r["cruk_refreshed"] is True
            b = c.get("/api/v1/summary").json()
            assert b["card_charity"] == round(a["card_total"] * 0.5, 2) and b["card_project"] == b["card_charity"]
            assert b["amount_to_pay_cruk"] == round(b["card_charity"] - b["moved_to_cruk"], 2)
            assert b["bmc_total_net"] == b["bmc_total"] and b["goal"] == 500 and b["goal_percent"] == round(b["total"] / 5, 1)
            assert c.post("/dash/api/settings", json={"cruk_goal": 500, "charity_percent": 101, "card_fee_percent": 0, "card_fee_fixed_pence": 0,
                                                       "bmc_fee_percent": 0, "bmc_fee_fixed_pence": 0}).status_code == 422
    finally:
        m.collect_cruk = orig


def test_transactions_log():
    with TestClient(app) as c:
        c.post("/api/v1/events/payment", headers=H, json={"id": "tx-log-1", "amount": "3.00"})
        c.post("/dash/api/bmc", json={"amount": "2.00", "note": "renewal"})
        d = c.get("/dash/api/transactions").json()
        types = {t["type"] for t in d["transactions"]}
        assert types == {"card", "coffee"} and d["count"] == len(d["transactions"])
        assert any(t["ref"] == "tx-log-1" and t["source"].startswith("Home Assistant") for t in d["transactions"])
        assert any(t["source"].startswith("manual") for t in d["transactions"])


def test_hide_and_manual_payment():
    with TestClient(app) as c:
        base = c.get("/api/v1/summary").json()
        c.post("/api/v1/events/payment", headers=H, json={"id": "hide-me-1", "amount": "50.00"})
        r = c.post("/dash/api/payments", json={"amount": "20.00", "note": "cash tin"}).json()
        s1 = c.get("/api/v1/summary").json()
        assert s1["card_total"] == round(base["card_total"] + 70.0, 2) and s1["manual_total"] == round(base["manual_total"] + 20.0, 2)
        # cash: no fees, same split as card
        cfg = s1["settings"]; pct = cfg["charity_percent"] / 100
        fee50 = round(50 * cfg["card_fee_percent"] / 100, 2) + cfg["card_fee_fixed_pence"] / 100
        assert abs((s1["card_charity"] - base["card_charity"]) - pct * (20 + 50 - fee50)) < 0.02
        tx = c.get("/dash/api/transactions").json()["transactions"]
        assert any(t["type"] == "manual" and t["est_fee"] == 0 for t in tx)
        assert c.post("/dash/api/transactions/card/hide-me-1/hidden", json={"hidden": True}).json()["ok"]
        s2 = c.get("/api/v1/summary").json()
        assert s2["card_total"] == round(base["card_total"] + 20.0, 2)
        c.post("/api/v1/events/payment", headers=H, json={"id": "hide-me-1", "amount": "50.00"})   # HA retry stays hidden
        assert c.get("/api/v1/summary").json()["card_total"] == s2["card_total"]
        assert c.post("/dash/api/transactions/card/hide-me-1/hidden", json={"hidden": False}).json()["ok"]
        assert c.get("/api/v1/summary").json()["card_total"] == s1["card_total"]
        assert c.delete(f"/dash/api/transactions/manual/{r['id']}").json()["ok"]
        assert c.get("/api/v1/summary").json()["card_total"] == round(base["card_total"] + 50.0, 2)
        assert c.post("/dash/api/payments", json={"amount": "5", "apply_fees": True}).json()["ok"]
        assert c.delete("/dash/api/transactions/card/nope").status_code == 404
