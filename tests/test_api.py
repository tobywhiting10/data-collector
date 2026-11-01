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
