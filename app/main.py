"""
Bussage central stats service.

Stores song plays and donations sent by Home Assistant, collects the online totals on a schedule,
does the money maths, and publishes a read-only JSON feed for the website.

Money is stored in integer pence. Events are append-only and idempotent (same id twice = stored once).
"""
import asyncio
import json
import hmac
import logging
import os
import re
import sqlite3
import uuid
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import httpx
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

log = logging.getLogger("bussage")
logging.basicConfig(level=logging.INFO)

# ------------------------------------------------------------------------------ configuration
DB_PATH = os.environ.get("DB_PATH", "/data/bussage.db")
SONGS_FILE = os.environ.get("SONGS_FILE", "/config/songs.yaml")
API_KEY = os.environ.get("API_KEY", "")                       # required: Home Assistant / admin writes
CHARITY_SHARE = Decimal(os.environ.get("CHARITY_SHARE", "0.90"))   # share of NET card money to the charity
FEE_PERCENT = Decimal(os.environ.get("FEE_PERCENT", "1.69"))       # SumUp pay-as-you-go rate (estimate)
FEE_FIXED_PENCE = int(os.environ.get("FEE_FIXED_PENCE", "0"))
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
COLLECT_INTERVAL_MIN = int(os.environ.get("COLLECT_INTERVAL_MIN", "60"))
BMC_WEBHOOK_SECRET = os.environ.get("BMC_WEBHOOK_SECRET", "")   # signing secret from the BMC webhook page
BMC_CURRENCY = os.environ.get("BMC_CURRENCY", "GBP").upper()
CRUK_URL = os.environ.get("CRUK_URL", "https://fundraise.cancerresearchuk.org/page/bussage-christmas-lights-show")
# Applied to the page TEXT (tags stripped). Group 1 = total raised, optional group 2 = Gift Aid.
# Matches e.g. "Total raised £12.50 + £3.12 Gift Aid". Override with CRUK_REGEX if the wording changes.
CRUK_REGEX = os.environ.get("CRUK_REGEX", r"Total raised\s*£\s*([\d,]+(?:\.\d+)?)(?:\s*\+\s*£\s*([\d,]+(?:\.\d+)?)\s*Gift Aid)?")
MIN_PENCE, MAX_PENCE = 1, 100_000  # sanity limits for one card payment: 1p .. £1,000

ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{3,100}$")


# ------------------------------------------------------------------------------ database
def db() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with closing(db()) as c, c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS plays(
              event_id TEXT PRIMARY KEY, file TEXT NOT NULL, started_at TEXT NOT NULL,
              seconds INTEGER NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS payments(
              id TEXT PRIMARY KEY, amount_pence INTEGER NOT NULL, ts TEXT NOT NULL,
              kind TEXT NOT NULL, tx_code TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS external(
              name TEXT PRIMARY KEY, total_pence INTEGER NOT NULL, updated_at TEXT NOT NULL,
              origin TEXT NOT NULL, note TEXT);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS transfers(
              id INTEGER PRIMARY KEY AUTOINCREMENT, amount_pence INTEGER NOT NULL, day TEXT NOT NULL,
              note TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS bmc(
              id INTEGER PRIMARY KEY AUTOINCREMENT, amount_pence INTEGER NOT NULL, day TEXT NOT NULL,
              note TEXT, created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_plays_started ON plays(started_at);
            """
        )


def migrate_db() -> None:
    """Add the webhook de-duplication column to databases created before it existed."""
    with closing(db()) as c, c:
        def add(table, col, ddl):
            if col not in [r["name"] for r in c.execute(f"PRAGMA table_info({table})")]:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        add("bmc", "ext_id", "TEXT")
        add("bmc", "hidden", "INTEGER NOT NULL DEFAULT 0")          # hidden rows are excluded from every calculation
        add("payments", "hidden", "INTEGER NOT NULL DEFAULT 0")
        add("payments", "manual", "INTEGER NOT NULL DEFAULT 0")     # 1 = entered by hand on /dash (e.g. cash)
        add("payments", "apply_fees", "INTEGER NOT NULL DEFAULT 1")  # 0 = no card fee for this payment
        add("payments", "note", "TEXT")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_bmc_ext ON bmc(ext_id)")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------------------ catalogue
_catalogue_cache: dict = {"mtime": None, "songs": []}


def load_catalogue() -> list[dict]:
    p = Path(SONGS_FILE)
    if not p.exists():
        return []
    mtime = p.stat().st_mtime
    if _catalogue_cache["mtime"] != mtime:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        songs = []
        for s in data.get("songs", []):
            if s.get("file"):
                songs.append({
                    "file": str(s["file"]), "title": str(s.get("title", s["file"])),
                    "artist": str(s.get("artist", "")), "category": str(s.get("category", "Other")),
                    "duration_seconds": s.get("duration_seconds"),
                })
        _catalogue_cache.update(mtime=mtime, songs=songs)
    return _catalogue_cache["songs"]


# ------------------------------------------------------------------------------ models / auth
def require_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    if not API_KEY or not x_api_key or not hmac.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="bad or missing X-API-Key")


class PlayIn(BaseModel):
    event_id: str
    file: str
    started_at: datetime
    seconds: int = Field(ge=0, le=6 * 3600)
    source: str = "unknown"   # visitor | scheduled | unknown


class PaymentIn(BaseModel):
    id: str                  # the donation attempt ID (= SumUp foreign_transaction_id)
    amount: Decimal          # pounds, e.g. 8.00
    ts: Optional[datetime] = None
    kind: str = "success"    # success | late
    tx_code: Optional[str] = None


class ExternalIn(BaseModel):
    total: Decimal           # pounds
    note: Optional[str] = None


def to_pence(d: Decimal) -> int:
    try:
        return int((d * 100).quantize(Decimal("1")))
    except InvalidOperation:
        raise HTTPException(status_code=422, detail="bad amount")


def pounds(pence: int) -> float:
    return round(pence / 100, 2)


# ------------------------------------------------------------------------------ stats
SETTING_KEYS = ("cruk_goal", "charity_percent", "card_fee_percent", "card_fee_fixed_pence", "bmc_fee_percent", "bmc_fee_fixed_pence")


def default_settings() -> dict:
    """Environment values are the defaults; anything saved on /dash overrides them."""
    return {"cruk_goal": float(os.environ.get("CRUK_GOAL", "1000")), "charity_percent": float(CHARITY_SHARE * 100), "card_fee_percent": float(FEE_PERCENT),
            "card_fee_fixed_pence": FEE_FIXED_PENCE,
            "bmc_fee_percent": float(os.environ.get("BMC_FEE_PERCENT", "5")),
            "bmc_fee_fixed_pence": int(os.environ.get("BMC_FEE_FIXED_PENCE", "0"))}


def get_settings() -> dict:
    out = default_settings()
    with closing(db()) as c:
        for r in c.execute("SELECT key, value FROM settings"):
            if r["key"] in out:
                out[r["key"]] = type(out[r["key"]])(float(r["value"]))
    return out


def fee_pence(amount_pence: int, percent: float, fixed_pence: int) -> int:
    return int((Decimal(amount_pence) * Decimal(str(percent)) / 100).quantize(Decimal("1"))) + fixed_pence


def compute_stats() -> dict:
    cat = load_catalogue()
    by_file = {s["file"]: s for s in cat}
    with closing(db()) as c:
        plays = c.execute("SELECT file, started_at, seconds, source FROM plays").fetchall()
        pays = c.execute("SELECT amount_pence, apply_fees, manual FROM payments WHERE hidden=0").fetchall()
        ext = {r["name"]: dict(r) for r in c.execute("SELECT * FROM external")}
        moved = c.execute("SELECT COALESCE(SUM(amount_pence),0) FROM transfers").fetchone()[0]
        bmc_rows = c.execute("SELECT amount_pence, day FROM bmc WHERE hidden=0").fetchall()
    cfg = get_settings()
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=61)).isoformat()
    bmc_fee = lambda a: fee_pence(a, cfg["bmc_fee_percent"], cfg["bmc_fee_fixed_pence"])
    bmc_all = sum(r["amount_pence"] for r in bmc_rows)
    bmc_all_fees = sum(bmc_fee(r["amount_pence"]) for r in bmc_rows)
    bmc_2m = sum(r["amount_pence"] for r in bmc_rows if r["day"] >= cutoff)
    bmc_2m_fees = sum(bmc_fee(r["amount_pence"]) for r in bmc_rows if r["day"] >= cutoff)

    # ---- songs
    per = {s["file"]: {"plays": 0, "seconds": 0, "last_played": None, "visitor_plays": 0} for s in cat}
    per_day: dict[str, int] = {}
    total_seconds = total_plays = 0
    for p in plays:
        if p["file"] not in per:      # no longer in the catalogue: keep the row, don't publish it
            continue
        e = per[p["file"]]
        e["plays"] += 1
        e["seconds"] += p["seconds"]
        e["visitor_plays"] += 1 if p["source"] == "visitor" else 0
        if e["last_played"] is None or p["started_at"] > e["last_played"]:
            e["last_played"] = p["started_at"]
        total_plays += 1
        total_seconds += p["seconds"]
        per_day[p["started_at"][:10]] = per_day.get(p["started_at"][:10], 0) + 1

    songs_out = []
    for s in cat:
        e = per[s["file"]]
        songs_out.append({"title": s["title"], "artist": s["artist"], "category": s["category"],
                          "file": s["file"], "plays": e["plays"], "visitor_plays": e["visitor_plays"],
                          "seconds_listened": e["seconds"], "last_played": e["last_played"]})
    songs_out.sort(key=lambda x: (-x["plays"], x["title"]))

    cats: dict[str, dict] = {}
    for s in songs_out:
        c_ = cats.setdefault(s["category"], {"category": s["category"], "plays": 0, "seconds_listened": 0})
        c_["plays"] += s["plays"]
        c_["seconds_listened"] += s["seconds_listened"]

    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]

    # ---- card money (integer pence)
    gross = sum(p["amount_pence"] for p in pays)
    fees = sum(fee_pence(p["amount_pence"], cfg["card_fee_percent"], cfg["card_fee_fixed_pence"])
               for p in pays if p["apply_fees"])
    manual_total = sum(p["amount_pence"] for p in pays if p["manual"])
    net = gross - fees
    charity_card = int((Decimal(net) * Decimal(str(cfg["charity_percent"])) / 100).quantize(Decimal("1")))
    project_card = net - charity_card

    cruk = ext.get("cruk_page", {}).get("total_pence", 0)
    # Card money is transferred by hand to the CRUK page, so it is already INSIDE the CRUK total
    # once transferred. The headline charity figure is therefore the CRUK page alone (incl. Gift Aid).
    raised_charity = cruk
    # Summary for the admin page / website. Headline total = what has been raised FOR CRUK:
    # the CRUK page (which already contains money moved there) + the charity share of card money not yet moved.
    #   total = CRUK + (card_charity - moved)
    total_pence = cruk + charity_card - moved
    goal = cfg["cruk_goal"]
    summary = {
        "total": pounds(total_pence),
        "goal": goal,
        "goal_percent": round(total_pence / 100 / goal * 100, 1) if goal > 0 else None,
        "cruk_only": pounds(cruk),
        "card_total": pounds(gross),                            # includes manually added (e.g. cash) payments
        "manual_total": pounds(manual_total),
        "card_charity": pounds(charity_card),
        "card_project": pounds(project_card),
        "amount_to_pay_cruk": pounds(charity_card - moved),     # charity share of card money not yet moved to CRUK
        "card_charity_pending": pounds(charity_card - moved),   # same value (older name)
        "moved_to_cruk": pounds(moved),
        "bmc_last_2_months": pounds(bmc_2m),                    # gross
        "bmc_last_2_months_net": pounds(bmc_2m - bmc_2m_fees),  # after the estimated BMC fee
        "bmc_total": pounds(bmc_all),
        "bmc_total_net": pounds(bmc_all - bmc_all_fees),
        "project_total": pounds(project_card + bmc_all - bmc_all_fees),   # project share of card + all BMC after fees
        "settings": cfg,
    }

    def ext_out(name):
        e = ext.get(name)
        if not e:
            return {"total": 0.0, "raised": 0.0, "gift_aid": 0.0, "updated_at": None, "origin": None}
        extra = {}
        try:
            extra = json.loads(e["note"] or "{}")
        except ValueError:
            pass
        return {"total": pounds(e["total_pence"]), "raised": extra.get("raised", pounds(e["total_pence"])),
                "gift_aid": extra.get("gift_aid", 0.0), "updated_at": e["updated_at"], "origin": e["origin"]}

    return {
        "generated_at": now_iso(),
        "listening": {
            "total_plays": total_plays,
            "total_seconds": total_seconds,
            "hours": total_seconds // 3600,
            "minutes": (total_seconds % 3600) // 60,
            "human": f"{total_seconds // 3600}h {(total_seconds % 3600) // 60}m",
        },
        "songs": songs_out,
        "top_songs": [s for s in songs_out if s["plays"] > 0][:10],
        "categories": sorted(cats.values(), key=lambda x: -x["plays"]),
        "plays_by_day": [{"date": d, "plays": per_day.get(d, 0)} for d in days],
        "donations": {
            "currency": "GBP",
            "card": {
                "payments": len(pays),
                "gross": pounds(gross),
                "estimated_fees": pounds(fees),
                "net_after_fees": pounds(net),
                "to_charity": pounds(charity_card),
                "to_project": pounds(project_card),
                "charity_share": cfg["charity_percent"] / 100,
                "fee_percent_assumed": cfg["card_fee_percent"],
            },
            "cancer_research_uk_page": ext_out("cruk_page"),
            "total_raised_for_charity": pounds(raised_charity),
            "summary": summary,
        },
    }


# ------------------------------------------------------------------------------ collectors
def set_external(name: str, total_pence: int, origin: str, note: Optional[str] = None) -> None:
    with closing(db()) as c, c:
        c.execute(
            "INSERT INTO external(name,total_pence,updated_at,origin,note) VALUES(?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET total_pence=excluded.total_pence, updated_at=excluded.updated_at, "
            "origin=excluded.origin, note=excluded.note",
            (name, total_pence, now_iso(), origin, note),
        )


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = (text.replace("&pound;", "£").replace("&#163;", "£").replace("&#xA3;", "£")
                .replace("&nbsp;", " ").replace("&amp;", "&").replace("\xa0", " "))
    return re.sub(r"\s+", " ", text)


def parse_cruk(html: str):
    """Returns (raised_pence, gift_aid_pence) or None if the wording is not found."""
    m = re.search(CRUK_REGEX, html_to_text(html), re.I)
    if not m:
        return None
    num = lambda g: to_pence(Decimal(re.sub(r"[^\d.]", "", g))) if g else 0
    return num(m.group(1)), (num(m.group(2)) if m.lastindex and m.lastindex >= 2 else 0)


async def collect_cruk(client: httpx.AsyncClient) -> bool:
    """Reads the public CRUK fundraising page; stores raised + Gift Aid. Once an hour is plenty."""
    if not CRUK_URL:
        return False
    r = await client.get(CRUK_URL, headers={"User-Agent": "BussageLightsStats/1.0 (+personal fundraising counter)"})
    r.raise_for_status()
    got = parse_cruk(r.text)
    if not got:
        log.warning("CRUK page text did not match CRUK_REGEX; keeping the previous total")
        return False
    raised, ga = got
    set_external("cruk_page", raised + ga, "scrape", json.dumps({"raised": pounds(raised), "gift_aid": pounds(ga)}))
    return True


async def collector_loop() -> None:
    await asyncio.sleep(5)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        while True:
            for fn in (collect_cruk,):
                try:
                    await fn(client)
                except Exception as e:  # one failing source must not stop the others
                    log.warning("collector %s failed: %s", fn.__name__, e)
            await asyncio.sleep(COLLECT_INTERVAL_MIN * 60)


# ------------------------------------------------------------------------------ app
@asynccontextmanager
async def lifespan(_: FastAPI):
    if not API_KEY or len(API_KEY) < 16:
        raise RuntimeError("API_KEY must be set (at least 16 characters)")
    init_db()
    migrate_db()
    load_catalogue()
    task = asyncio.create_task(collector_loop())
    yield
    task.cancel()


app = FastAPI(title="Bussage stats", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET"], allow_headers=["*"])


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/v1/stats")
def stats(response: Response):
    response.headers["Cache-Control"] = "public, max-age=60"
    return compute_stats()


@app.get("/api/v1/summary")
def summary(response: Response):
    response.headers["Cache-Control"] = "public, max-age=60"
    return compute_stats()["donations"]["summary"]


@app.get("/api/v1/songs")
def songs(response: Response):
    response.headers["Cache-Control"] = "public, max-age=60"
    cat = load_catalogue()
    return {"songs": cat, "files": [s["file"] for s in cat]}


def _button(song: dict) -> dict:
    """One dashboard song button, identical in look to the hand-made ones."""
    f = song["file"]
    name = f'{song["artist"]} - {song["title"]}' if song["artist"] else song["title"]
    return {
        "type": "custom:button-card", "entity": "input_select.christmas_songs", "name": name, "icon": "mdi:music",
        "tap_action": {"action": "call-service", "service": "input_select.select_option",
                       "service_data": {"entity_id": "input_select.christmas_songs", "option": f}},
        "state": [{"value": f, "color": "green", "icon": "mdi:music-note"}, {"value": "default", "color": "grey"}],
        "extra_styles": ":host {\n  display: block;\n  width: 100%;\n  min-width: 0;\n}\n",
        "styles": {
            "card": [{"width": "calc(25vw - 44px)"}, {"min-width": 0}, {"min-height": "100px"},
                     {"box-sizing": "border-box"}, {"padding": "8px"}],
            "name": [{"white-space": "normal"}, {"overflow": "visible"}, {"text-overflow": "clip"},
                     {"text-align": "center"}, {"line-height": 1.2}, {"font-size": "clamp(14px, 1.5vw, 22px)"}],
        },
    }


@app.get("/api/v1/songs/buttons.yaml", response_class=Response)
def song_buttons_yaml():
    """Ready-to-paste dashboard YAML: a heading + one button per song, grouped by category.
    Open this URL, copy a category block into the dashboard's `cards:` list of a column."""
    groups: dict[str, list] = {}
    for song in load_catalogue():
        groups.setdefault(song["category"], []).append(song)
    out = ["# Generated from songs.yaml. Paste a block into the `cards:` list of a column\n"
           "# (inside the vertical-stack). Re-open this URL after adding songs.\n"]
    for cat, songs in groups.items():
        cards = [{"type": "heading", "heading": cat, "heading_style": "title"}] + [_button(x) for x in songs]
        out.append(f"\n# ---- {cat} ----\n" + yaml.safe_dump(cards, sort_keys=False, allow_unicode=True))
    return Response("".join(out), media_type="text/plain; charset=utf-8")


@app.post("/api/v1/events/play", dependencies=[Depends(require_key)])
def post_play(p: PlayIn):
    if not ID_RE.match(p.event_id):
        raise HTTPException(422, "bad event_id")
    if p.file not in {s["file"] for s in load_catalogue()}:
        return {"stored": False, "reason": "file not in catalogue"}
    with closing(db()) as c, c:
        cur = c.execute(
            "INSERT OR IGNORE INTO plays(event_id,file,started_at,seconds,source,created_at) VALUES(?,?,?,?,?,?)",
            (p.event_id, p.file, p.started_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
             p.seconds, p.source[:20], now_iso()),
        )
    return {"stored": cur.rowcount == 1, "duplicate": cur.rowcount == 0}


@app.post("/api/v1/events/payment", dependencies=[Depends(require_key)])
def post_payment(p: PaymentIn):
    if not ID_RE.match(p.id):
        raise HTTPException(422, "bad id")
    pence = to_pence(p.amount)
    if not (MIN_PENCE <= pence <= MAX_PENCE):
        raise HTTPException(422, "amount out of range")
    ts = (p.ts or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as c, c:
        cur = c.execute(
            "INSERT OR IGNORE INTO payments(id,amount_pence,ts,kind,tx_code,created_at) VALUES(?,?,?,?,?,?)",
            (p.id, pence, ts, p.kind[:20], p.tx_code, now_iso()),
        )
    return {"stored": cur.rowcount == 1, "duplicate": cur.rowcount == 0}


@app.post("/api/v1/external/{name}", dependencies=[Depends(require_key)])
def post_external(name: str, body: ExternalIn):
    """Manual override / entry: name = cruk_page (total INCLUDING Gift Aid)."""
    if name != "cruk_page":
        raise HTTPException(404, "unknown source")
    set_external(name, to_pence(body.total), "manual", body.note)
    return {"ok": True}


@app.post("/api/v1/reload", dependencies=[Depends(require_key)])
def reload_catalogue():
    _catalogue_cache["mtime"] = None
    return {"songs": len(load_catalogue())}


# ------------------------------------------------------------------------------ /dash (admin page)
# NO authentication here by design: protect the whole /dash prefix (page AND /dash/api/*) at the reverse proxy.
from .dash_page import DASH_HTML  # noqa: E402


class MoneyIn(BaseModel):
    amount: Decimal
    date: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=120)


class CrukIn(BaseModel):
    total: Decimal


def _day(d: Optional[str]) -> str:
    if not d:
        return datetime.now(timezone.utc).date().isoformat()
    try:
        return datetime.strptime(d, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")


def _money_rows(table: str) -> list[dict]:
    with closing(db()) as c:
        where = "WHERE hidden=0" if table == "bmc" else ""
        rows = c.execute(f"SELECT id, amount_pence, day, note FROM {table} {where} ORDER BY day DESC, id DESC LIMIT 200").fetchall()
    return [{"id": r["id"], "amount": pounds(r["amount_pence"]), "date": r["day"], "note": r["note"]} for r in rows]


def _add_money(table: str, m: MoneyIn) -> dict:
    pence = to_pence(m.amount)
    if not (1 <= pence <= 10_000_000):
        raise HTTPException(422, "amount out of range")
    with closing(db()) as c, c:
        c.execute(f"INSERT INTO {table}(amount_pence,day,note,created_at) VALUES(?,?,?,?)",
                  (pence, _day(m.date), (m.note or "").strip() or None, now_iso()))
    return {"ok": True}


@app.get("/dash", response_class=Response)
def dash_page():
    return Response(DASH_HTML, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


@app.get("/dash/api/state")
def dash_state(response: Response):
    response.headers["Cache-Control"] = "no-store"
    st = compute_stats()
    with closing(db()) as c:
        npay = c.execute("SELECT COUNT(*) FROM payments WHERE hidden=0").fetchone()[0]
    return {"summary": st["donations"]["summary"], "cruk": st["donations"]["cancer_research_uk_page"],
            "transfers": _money_rows("transfers"), "bmc": _money_rows("bmc"), "card_payments": npay,
            "total_plays": st["listening"]["total_plays"], "generated_at": st["generated_at"]}


@app.post("/dash/api/transfers")
def dash_add_transfer(m: MoneyIn):
    return _add_money("transfers", m)


@app.post("/dash/api/bmc")
def dash_add_bmc(m: MoneyIn):
    return _add_money("bmc", m)


@app.delete("/dash/api/transfers/{rid}")
def dash_del_transfer(rid: int):
    with closing(db()) as c, c:
        c.execute("DELETE FROM transfers WHERE id=?", (rid,))
    return {"ok": True}


@app.delete("/dash/api/bmc/{rid}")
def dash_del_bmc(rid: int):
    with closing(db()) as c, c:
        c.execute("DELETE FROM bmc WHERE id=?", (rid,))
    return {"ok": True}


@app.post("/dash/api/cruk")
def dash_set_cruk(b: CrukIn):
    pence = to_pence(b.total)
    if not (0 <= pence <= 100_000_000):
        raise HTTPException(422, "amount out of range")
    set_external("cruk_page", pence, "manual", json.dumps({"raised": pounds(pence), "gift_aid": 0.0}))
    return {"ok": True}


# ------------------------------------------------------------------------------ Buy Me a Coffee webhook
# Public URL (BMC cannot send our API key); authenticity is proven by the HMAC-SHA256 signature of the raw body.
# Counts one-time payments (donations, shop items, commissions, wishlist). Refund events remove the entry.
# Recurring donations/memberships only send start/update/cancel events (no per-renewal event), so they are NOT counted.
from fastapi import Request  # noqa: E402

BMC_PAYMENT_EVENTS = {"donation.created", "extra_purchase.created", "commission_order.created", "wishlist_payment.created"}
BMC_REFUND_EVENTS = {"donation.refunded", "extra_purchase.refunded", "commission_order.refunded", "wishlist_payment.refunded"}


def _bmc_signature_ok(raw: bytes, header: Optional[str]) -> bool:
    if not BMC_WEBHOOK_SECRET or not header:
        return False
    want = hmac.new(BMC_WEBHOOK_SECRET.encode(), raw, "sha256").hexdigest()
    got = header.strip().lower().removeprefix("sha256=")
    return hmac.compare_digest(want, got)


@app.post("/api/v1/webhooks/bmc")
async def bmc_webhook(request: Request, x_signature_sha256: Optional[str] = Header(default=None)):
    if not BMC_WEBHOOK_SECRET:
        raise HTTPException(503, "BMC_WEBHOOK_SECRET is not configured")
    raw = await request.body()
    if not _bmc_signature_ok(raw, x_signature_sha256):
        raise HTTPException(401, "bad signature")
    try:
        ev = json.loads(raw)
        etype, data = str(ev.get("type", "")), ev.get("data") or {}
    except (ValueError, AttributeError):
        raise HTTPException(400, "bad json")
    if ev.get("live_mode") is False:
        return {"ok": True, "ignored": "test event (signature verified)"}
    pid = data.get("id")
    if etype in BMC_PAYMENT_EVENTS and pid is not None:
        if str(data.get("currency", "")).upper() != BMC_CURRENCY:
            log.warning("BMC event %s ignored: currency %s != %s", ev.get("event_id"), data.get("currency"), BMC_CURRENCY)
            return {"ok": True, "ignored": "currency"}
        pence = to_pence(Decimal(str(data.get("amount", 0))))
        if pence <= 0:
            return {"ok": True, "ignored": "zero amount"}
        created = data.get("created_at") or ev.get("created")
        day = datetime.fromtimestamp(int(created), timezone.utc).date().isoformat() if created else _day(None)
        with closing(db()) as c, c:
            cur = c.execute("INSERT OR IGNORE INTO bmc(amount_pence,day,note,created_at,ext_id) VALUES(?,?,?,?,?)",
                            (pence, day, f"webhook {etype}", now_iso(), f"payment:{pid}"))
        return {"ok": True, "stored": cur.rowcount == 1}
    if etype in BMC_REFUND_EVENTS and pid is not None:
        with closing(db()) as c, c:
            cur = c.execute("DELETE FROM bmc WHERE ext_id=?", (f"payment:{pid}",))
        return {"ok": True, "removed": cur.rowcount}
    return {"ok": True, "ignored": etype or "unknown"}


class SettingsIn(BaseModel):
    cruk_goal: float = Field(ge=0, le=10_000_000)
    charity_percent: float = Field(ge=0, le=100)          # share of card money (after fees) that goes to CRUK
    card_fee_percent: float = Field(ge=0, le=20)
    card_fee_fixed_pence: int = Field(ge=0, le=100)
    bmc_fee_percent: float = Field(ge=0, le=30)
    bmc_fee_fixed_pence: int = Field(ge=0, le=100)


@app.get("/dash/api/settings")
def dash_get_settings():
    return get_settings()


@app.post("/dash/api/settings")
async def dash_set_settings(b: SettingsIn):
    with closing(db()) as c, c:
        for k in SETTING_KEYS:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (k, str(getattr(b, k))))
    # Everything is calculated on demand, so totals already reflect the new settings.
    # Also pull the latest CRUK figure right now.
    refreshed, error = False, None
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            refreshed = await collect_cruk(client)
            if not refreshed:
                error = "CRUK page could not be read (kept the previous figure); see the container log"
    except Exception as e:  # noqa: BLE001
        error = f"CRUK refresh failed: {e}"
        log.warning(error)
    return {"ok": True, "cruk_refreshed": refreshed, "cruk_error": error}


@app.get("/dash/api/transactions")
def dash_transactions(response: Response, limit: int = 500):
    """Every card payment (Home Assistant, one row each), manual payment and Buy Me a Coffee entry, including hidden ones."""
    response.headers["Cache-Control"] = "no-store"
    limit = max(1, min(limit, 2000))
    cfg = get_settings()
    out = []
    with closing(db()) as c:
        for r in c.execute("SELECT id, amount_pence, ts, kind, hidden, manual, apply_fees, note FROM payments"):
            f = fee_pence(r["amount_pence"], cfg["card_fee_percent"], cfg["card_fee_fixed_pence"]) if r["apply_fees"] else 0
            if r["manual"]:
                src = "manual" + (", fees apply" if r["apply_fees"] else ", no fees") + (f" - {r['note']}" if r["note"] else "")
            else:
                src = "Home Assistant" + (" (late)" if r["kind"] == "late" else "")
            out.append({"type": "manual" if r["manual"] else "card", "key": r["id"], "when": r["ts"], "ref": r["id"], "source": src,
                        "hidden": bool(r["hidden"]), "amount": pounds(r["amount_pence"]), "est_fee": pounds(f),
                        "est_net": pounds(r["amount_pence"] - f)})
        for r in c.execute("SELECT id, amount_pence, day, ext_id, note, hidden FROM bmc"):
            f = fee_pence(r["amount_pence"], cfg["bmc_fee_percent"], cfg["bmc_fee_fixed_pence"])
            out.append({"type": "coffee", "key": str(r["id"]), "when": r["day"], "ref": r["ext_id"] or f"manual-{r['id']}",
                        "source": "webhook" if r["ext_id"] else "manual" + (f" - {r['note']}" if r["note"] else ""),
                        "hidden": bool(r["hidden"]), "amount": pounds(r["amount_pence"]), "est_fee": pounds(f),
                        "est_net": pounds(r["amount_pence"] - f)})
    out.sort(key=lambda x: x["when"], reverse=True)
    return {"count": len(out), "shown": min(len(out), limit), "transactions": out[:limit]}


class HideIn(BaseModel):
    hidden: bool


def _tx_table(kind: str) -> str:
    if kind in ("card", "manual"):
        return "payments"
    if kind == "coffee":
        return "bmc"
    raise HTTPException(404, "unknown transaction type")


@app.post("/dash/api/transactions/{kind}/{key}/hidden")
def dash_hide(kind: str, key: str, b: HideIn):
    """Hidden rows stay in the log but are excluded from every total."""
    t = _tx_table(kind)
    with closing(db()) as c, c:
        cur = c.execute(f"UPDATE {t} SET hidden=? WHERE {'id' if t == 'bmc' else 'id'}=?", (1 if b.hidden else 0, key))
    if not cur.rowcount:
        raise HTTPException(404, "not found")
    return {"ok": True}


@app.delete("/dash/api/transactions/{kind}/{key}")
def dash_delete_tx(kind: str, key: str):
    """Permanent. For Home Assistant payments prefer hiding: a later retry of the same payment would re-add a deleted one."""
    t = _tx_table(kind)
    with closing(db()) as c, c:
        cur = c.execute(f"DELETE FROM {t} WHERE id=?", (key,))
    if not cur.rowcount:
        raise HTTPException(404, "not found")
    return {"ok": True}


class ManualPaymentIn(BaseModel):
    amount: Decimal
    date: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=120)
    apply_fees: bool = False       # cash: no card fee. Tick for a card payment the reader app did not report.


@app.post("/dash/api/payments")
def dash_add_payment(m: ManualPaymentIn):
    """Manual payment (e.g. cash in hand). Counted in the card total and split exactly like card payments."""
    pence = to_pence(m.amount)
    if not (1 <= pence <= 1_000_000):
        raise HTTPException(422, "amount out of range")
    day = _day(m.date)
    ts = now_iso() if day == datetime.now(timezone.utc).date().isoformat() else f"{day}T12:00:00+00:00"
    pid = "manual-" + uuid.uuid4().hex[:12]
    with closing(db()) as c, c:
        c.execute("INSERT INTO payments(id,amount_pence,ts,kind,tx_code,created_at,manual,apply_fees,note) VALUES(?,?,?,?,?,?,?,?,?)",
                  (pid, pence, ts, "manual", None, now_iso(), 1, 1 if m.apply_fees else 0, (m.note or "").strip() or None))
    return {"ok": True, "id": pid}
