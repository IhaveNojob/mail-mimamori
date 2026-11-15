"""Webhook, history page, scheduled jobs, and (DEMO_MODE) a browser LINE simulator.

Run:  uvicorn app.main:app --reload   ->  open http://localhost:8000/demo
"""
import asyncio
import html
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from . import analyzer, config, db, line, messages, service

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"


async def scheduler():
    while True:
        try:
            await run_in_threadpool(service.run_scheduled)
        except Exception:
            log.exception("scheduled job failed")
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.conn()
    task = asyncio.create_task(scheduler())
    yield
    task.cancel()


app = FastAPI(title="親宛て郵便物の見える化 PoC", lifespan=lifespan)


@app.get("/")
def root():
    return RedirectResponse("/demo" if config.DEMO_MODE else "/health")


@app.get("/health")
def health():
    return {"ok": True, "line_mode": config.LINE_MODE, "analyzer": config.ANALYZER, "demo": config.DEMO_MODE,
            "worker_online": worker_online() if config.ANALYZER == "remote" or config.LINE_MODE == "relay" else None}


# ---------- LINE webhook ----------

def _handle_event(ev: dict):
    uid = ev.get("source", {}).get("userId")
    token = ev.get("replyToken")
    if not uid:
        return
    t = ev.get("type")
    if t == "follow":
        line.reply(token, uid, "友だち追加ありがとうございます。\n" + messages.HELP)
    elif t == "postback":
        service.handle_postback(uid, ev["postback"]["data"], token)
    elif t == "message":
        msg = ev["message"]
        if msg["type"] == "text":
            service.handle_text(uid, msg["text"], token)
        elif msg["type"] == "image":
            if config.LINE_MODE == "relay":
                db.ex("INSERT INTO line_fetch (user_id, message_id, state, created_at) VALUES (?,?,?,?)", (uid, msg["id"], "queued", db.now_iso()))
            else:
                service.handle_image(uid, line.get_content(msg["id"]), token)


@app.post("/callback")
async def callback(request: Request):
    body = await request.body()
    if not line.verify_signature(body, request.headers.get("x-line-signature", "")):
        raise HTTPException(400, "bad signature")
    for ev in json.loads(body).get("events", []):
        try:
            await run_in_threadpool(_handle_event, ev)
        except Exception:
            log.exception("event failed: %s", ev.get("type"))
    return {"ok": True}


# ---------- remote analysis worker (ANALYZER=remote) ----------

WORKER = {"last_seen": None}


def _worker_auth(request: Request):
    if not config.WORKER_TOKEN or request.headers.get("authorization") != f"Bearer {config.WORKER_TOKEN}":
        raise HTTPException(401)
    WORKER["last_seen"] = db.now()


@app.get("/worker/jobs")
def worker_jobs(request: Request):
    _worker_auth(request)
    return {"jobs": service.claim_jobs() if config.ANALYZER == "remote" else [], **service.claim_line_work()}


@app.post("/worker/image/{fetch_id}")
async def worker_image(fetch_id: int, request: Request):
    _worker_auth(request)
    data = await request.body()
    return {"ok": await run_in_threadpool(service.fetched_image, fetch_id, data)}


@app.post("/worker/result/{mail_id}")
async def worker_result(mail_id: int, request: Request):
    _worker_auth(request)
    body = await request.json()
    r = analyzer.FAILED if body.get("error") else body
    return {"ok": await run_in_threadpool(service.finalize, mail_id, r)}


def worker_online() -> bool:
    return WORKER["last_seen"] is not None and (db.now() - WORKER["last_seen"]).total_seconds() < 15


# ---------- history page (token URL) ----------

def _family_by_token(token: str):
    f = db.one("SELECT * FROM family WHERE history_token=?", (token,))
    if not f:
        raise HTTPException(404)
    return f


@app.get("/history/{token}", response_class=HTMLResponse)
def history(token: str):
    f = _family_by_token(token)
    items = db.q("SELECT * FROM mail_item WHERE family_id=? AND state='analyzed' ORDER BY id DESC", (f["id"],))
    cards = []
    for m in items:
        imgs = db.q("SELECT id FROM mail_image WHERE mail_id=? ORDER BY id", (m["id"],))
        thumbs = "".join(f'<img src="/history/{token}/img/{i["id"]}" alt="">' for i in imgs)
        e = lambda v: html.escape(str(v)) if v not in (None, "") else "-"
        cards.append(f"""
        <article class="card imp-{e(m['importance'])}">
          <header><span class="badge">{e(m['importance'])}{' ・ ⚠️ 詐欺の疑い' if m['scam_risk'] else ''}</span><span class="st">{e(m['status'])}</span></header>
          {f'<p class="scam">注意: {e(m["scam_reason"])}</p>' if m['scam_risk'] else ''}
          <h2>{e(m['sender'])} <small>{e(m['kind'])}</small></h2>
          <p>{e(m['summary'])}</p>
          <dl><dt>金額</dt><dd>{messages.yen(m['amount'])}</dd><dt>期限</dt><dd>{e(m['due_date'])}</dd><dt>やること</dt><dd>{e(m['action'])}</dd></dl>
          <div class="thumbs">{thumbs}</div>
          <footer>受付 {e(m['created_at'][:16].replace('T', ' '))}</footer>
        </article>""")
    page = (STATIC / "history.html").read_text(encoding="utf-8")
    return page.replace("{{PARENT}}", html.escape(f["parent_name"])).replace("{{CARDS}}", "".join(cards) or "<p>まだ郵便はありません。</p>")


@app.get("/history/{token}/img/{image_id}")
def history_image(token: str, image_id: int):
    f = _family_by_token(token)
    r = db.one("SELECT mi.path FROM mail_image mi JOIN mail_item i ON i.id=mi.mail_id WHERE mi.id=? AND i.family_id=?", (image_id, f["id"]))
    if not r:
        raise HTTPException(404)
    with open(r["path"], "rb") as fh:
        head = fh.read(16)
    return FileResponse(r["path"], media_type=analyzer._media_type(head))


# ---------- demo simulator ----------

DEMO_USERS = {"U_child": "子ども（登録者）", "U_helper": "撮影する人（現地）", "U_sibling": "兄弟（家族）"}


def _demo_guard():
    if not config.DEMO_MODE:
        raise HTTPException(404)


def _log_input(uid: str, text: str):
    db.log_notification(f"from:{uid}", text)


@app.get("/demo", response_class=HTMLResponse)
def demo():
    _demo_guard()
    return (STATIC / "demo.html").read_text(encoding="utf-8")


@app.get("/demo/state")
def demo_state(after: int = 0):
    _demo_guard()
    rows = db.q("SELECT * FROM notification_log WHERE id>? ORDER BY id", (after,))
    out = []
    for r in rows:
        mine = r["to_user"].startswith("from:")
        uid = r["to_user"][5:] if mine else r["to_user"]
        if uid in DEMO_USERS:
            out.append({"id": r["id"], "user": uid, "mine": mine, "text": r["text"], "buttons": json.loads(r["buttons_json"] or "null"), "at": r["created_at"][11:16]})
    hist = None
    m = service.member_of("U_child")
    if m:
        hist = f"/history/{m['history_token']}"
    pending = len(db.q("SELECT id FROM mail_item WHERE state='collecting'"))
    pending += len(db.q("SELECT id FROM mail_item WHERE state IN ('queued','working','analyzing')"))
    return {"messages": out, "history": hist, "pending": pending, "analyzer": config.ANALYZER, "window": config.BATCH_WINDOW_SEC,
            "worker": worker_online() if config.ANALYZER == "remote" else None}


@app.post("/demo/text")
def demo_text(user: str = Form(...), text: str = Form(...)):
    _demo_guard()
    _log_input(user, text)
    service.handle_text(user, text)
    return {"ok": True}


SAMPLE_IMAGES = {"img:tax": ("tax.jpg", 1), "img:dm": ("dm.jpg", 3), "img:clinic": ("clinic.jpg", 4), "img:scam": ("scam.jpg", 6)}  # (file, fake hint)


@app.post("/demo/image")
async def demo_image(user: str = Form(...), sample: str = Form(""), file: UploadFile | None = File(None)):
    _demo_guard()
    hint = None
    if file and file.filename:
        data, label = await file.read(), f"（{file.filename}）"
    elif sample in SAMPLE_IMAGES:
        name, hint = SAMPLE_IMAGES[sample]
        data, label = (STATIC / "samples" / name).read_bytes(), ""
    else:
        data, label = (STATIC / "sample_mail.png").read_bytes(), ""
    _log_input(user, "📷 写真" + label)
    await run_in_threadpool(service.handle_image, user, data, None, hint)
    return {"ok": True}


@app.post("/demo/postback")
def demo_postback(user: str = Form(...), data: str = Form(...), label: str = Form("")):
    _demo_guard()
    _log_input(user, label or data)
    service.handle_postback(user, data)
    return {"ok": True}


@app.post("/demo/run/{job}")
async def demo_run(job: str):
    _demo_guard()
    fn = {"batch": lambda: service.process_due_batches(force=True), "trash": service.send_trash_digest, "remind": service.send_reminders}.get(job)
    if not fn:
        raise HTTPException(404)
    return {"ok": True, "count": await run_in_threadpool(fn)}


@app.post("/demo/setup")
def demo_setup():
    """One click: child registers, helper and sibling join with the invite codes."""
    _demo_guard()
    if service.member_of("U_child"):
        return {"ok": True, "already": True}
    _log_input("U_child", "登録 母")
    service.handle_text("U_child", "登録 母")
    f = db.one("SELECT f.* FROM family f JOIN member m ON m.family_id=f.id WHERE m.line_user_id='U_child'")
    for uid, code in (("U_helper", f["photographer_code"]), ("U_sibling", f["family_code"])):
        _log_input(uid, f"参加 {code}")
        service.handle_text(uid, f"参加 {code}")
    return {"ok": True}


@app.post("/demo/reset")
def demo_reset():
    _demo_guard()
    """Clear the simulator's data only. Families registered through real LINE are kept."""
    users = list(DEMO_USERS)
    marks = ",".join("?" * len(users))
    fams = [r["family_id"] for r in db.q(f"SELECT DISTINCT family_id FROM member WHERE line_user_id IN ({marks})", users)]
    for fid in fams:
        for img in db.q("SELECT mi.id, mi.path FROM mail_image mi JOIN mail_item i ON i.id=mi.mail_id WHERE i.family_id=?", (fid,)):
            Path(img["path"]).unlink(missing_ok=True)
            db.ex("DELETE FROM mail_image WHERE id=?", (img["id"],))
        db.ex("DELETE FROM mail_item WHERE family_id=?", (fid,))
        db.ex("DELETE FROM member WHERE family_id=?", (fid,))
        db.ex("DELETE FROM family WHERE id=?", (fid,))
    db.ex(f"DELETE FROM notification_log WHERE to_user IN ({marks})", users)
    db.ex(f"DELETE FROM notification_log WHERE to_user IN ({marks})", [f"from:{u}" for u in users])
    return {"ok": True, "families": len(fams)}
