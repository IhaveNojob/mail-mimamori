"""Business logic: intake, batching, analysis, notification, reminders."""
import json
import logging
import secrets
from datetime import date, datetime, timedelta

from . import analyzer, config, db, line, messages

log = logging.getLogger(__name__)


# ---------- members ----------

def member_of(user_id: str):
    return db.one("SELECT m.*, f.parent_name, f.history_token FROM member m JOIN family f ON f.id=m.family_id WHERE m.line_user_id=?", (user_id,))


def recipients(family_id: int) -> list[str]:
    return [r["line_user_id"] for r in db.q("SELECT line_user_id FROM member WHERE family_id=? AND role IN ('owner','family')", (family_id,))]


def _code() -> str:
    return secrets.token_hex(3).upper()


def register(user_id: str, parent_name: str) -> str:
    if member_of(user_id):
        return "すでに登録済みです。「一覧」で状況を確認できます。"
    pc, fc = _code(), _code()
    fid = db.ex(
        "INSERT INTO family (parent_name, photographer_code, family_code, history_token, created_at) VALUES (?,?,?,?,?)",
        (parent_name, pc, fc, secrets.token_urlsafe(16), db.now_iso()),
    )
    db.ex("INSERT INTO member (family_id, line_user_id, role, created_at) VALUES (?,?,?,?)", (fid, user_id, "owner", db.now_iso()))
    return messages.registered(parent_name, pc, fc)


def join(user_id: str, code: str) -> str:
    code = code.strip().upper()
    f = db.one("SELECT * FROM family WHERE photographer_code=? OR family_code=?", (code, code))
    if not f:
        return "招待コードが見つかりません。もう一度確認してください。"
    if member_of(user_id):
        return "すでにどこかの家族に参加しています。"
    role = "photographer" if f["photographer_code"] == code else "family"
    db.ex("INSERT INTO member (family_id, line_user_id, role, created_at) VALUES (?,?,?,?)", (f["id"], user_id, role, db.now_iso()))
    if role == "photographer":
        return f"{f['parent_name']}さん宛ての郵便を撮影する人として参加しました。郵便物の写真を送ってください（複数枚OK）。"
    return f"{f['parent_name']}さん宛ての郵便の通知を受け取る家族として参加しました。"


def list_items(user_id: str) -> str:
    m = member_of(user_id)
    if not m:
        return messages.HELP
    rows = db.q(
        "SELECT * FROM mail_item WHERE family_id=? AND state='analyzed' AND status IN ('未対応','あとで') ORDER BY due_date IS NULL, due_date, id DESC LIMIT 10",
        (m["family_id"],),
    )
    body = "\n".join(f"{'⚠️' if r['scam_risk'] else messages.ICON.get(r['importance'], '')} {r['sender']}（{r['kind']}）{('期限 ' + r['due_date']) if r['due_date'] else ''}［{r['status']}］" for r in rows) or "未対応の郵便はありません。"
    return f"{m['parent_name']}さん宛ての未対応の郵便\n{body}\n\n履歴: {messages.history_url(m['history_token'])}"


# ---------- intake ----------

def handle_text(user_id: str, text: str, reply_token: str | None = None):
    t = text.strip().replace("　", " ")
    if t.startswith("登録"):
        name = t[2:].strip() or "親"
        out = register(user_id, name)
    elif t.startswith("参加"):
        out = join(user_id, t[2:])
    elif t in ("一覧", "リスト"):
        out = list_items(user_id)
    else:
        out = messages.HELP
    line.reply(reply_token, user_id, out)


def handle_image(user_id: str, data: bytes, reply_token: str | None = None, hint: int | None = None):
    m = member_of(user_id)
    if not m:
        return line.reply(reply_token, user_id, "先に「参加 コード」で参加してください。")
    now = db.now()
    cutoff = (now - timedelta(seconds=config.BATCH_WINDOW_SEC)).isoformat(timespec="seconds")
    item = db.one(
        "SELECT * FROM mail_item WHERE family_id=? AND state='collecting' AND last_image_at>=? ORDER BY id DESC LIMIT 1",
        (m["family_id"], cutoff),
    )
    first = item is None
    if first:
        mail_id = db.ex(
            "INSERT INTO mail_item (family_id, state, raw_json, created_at, last_image_at) VALUES (?,?,?,?,?)",
            (m["family_id"], "collecting", json.dumps({"hint": hint}), now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")),
        )
    else:
        mail_id = item["id"]
        db.ex("UPDATE mail_item SET last_image_at=? WHERE id=?", (now.isoformat(timespec="seconds"), mail_id))
    n = len(db.q("SELECT id FROM mail_image WHERE mail_id=?", (mail_id,))) + 1
    path = config.DATA_DIR / "images" / f"{mail_id}_{n}.jpg"
    path.write_bytes(data)
    db.ex("INSERT INTO mail_image (mail_id, path, created_at) VALUES (?,?,?)", (mail_id, str(path), now.isoformat(timespec="seconds")))
    if first:
        line.reply(reply_token, user_id, f"受け取りました。{config.BATCH_WINDOW_SEC}秒以内に続けて送った写真は1通としてまとめます。")


def handle_postback(user_id: str, data: str, reply_token: str | None = None):
    if not data.startswith("status="):
        return
    mail_id_s, status = data[len("status="):].split(":", 1)
    m = member_of(user_id)
    item = db.one("SELECT * FROM mail_item WHERE id=?", (int(mail_id_s),))
    if not m or not item or item["family_id"] != m["family_id"] or status not in ("対応済み", "あとで", "不要"):
        return
    db.ex("UPDATE mail_item SET status=? WHERE id=?", (status, item["id"]))
    for uid in recipients(m["family_id"]):
        if uid != user_id:
            line.push(uid, f"「{item['sender']}（{item['kind']}）」が「{status}」になりました。")
    line.reply(reply_token, user_id, f"「{item['sender']}」を「{status}」にしました。")


# ---------- jobs ----------

def _images(mail_id: int) -> list[bytes]:
    return [open(r["path"], "rb").read() for r in db.q("SELECT path FROM mail_image WHERE mail_id=? ORDER BY id", (mail_id,))]


def finalize(mail_id: int, r: dict):
    """Store a (corrected) analysis result and notify. Ignored if already analyzed."""
    item = db.one("SELECT * FROM mail_item WHERE id=?", (mail_id,))
    if not item or item["state"] == "analyzed":
        return False
    r = analyzer.correct(r)
    db.ex(
        "UPDATE mail_item SET state='analyzed', sender=?, kind=?, summary=?, amount=?, due_date=?, action=?, importance=?, scam_risk=?, scam_reason=?, raw_json=?, notified_at=? WHERE id=?",
        (r["sender"], r["kind"], r["summary"], r["amount"], r["due_date"], r["action"], r["importance"],
         int(r["scam_risk"]), r["scam_reason"], json.dumps(r, ensure_ascii=False), db.now_iso(), mail_id),
    )
    notify_item(mail_id)
    return True


def process_due_batches(force: bool = False) -> int:
    """Analyze mail whose photo window has closed, then notify family.
    In remote mode, closed batches are queued for the worker; stale ones fall back to the mock."""
    now = db.now()
    cutoff = (now - timedelta(seconds=config.BATCH_WINDOW_SEC)).isoformat(timespec="seconds")
    sql = "SELECT * FROM mail_item WHERE state='collecting'" + ("" if force else " AND last_image_at<?")
    items = db.q(sql, () if force else (cutoff,))
    if config.ANALYZER == "remote":
        for item in items:
            db.ex("UPDATE mail_item SET state='queued' WHERE id=?", (item["id"],))
        stale = (now - timedelta(seconds=config.BATCH_WINDOW_SEC + config.REMOTE_TIMEOUT_SEC)).isoformat(timespec="seconds")
        items = db.q("SELECT * FROM mail_item WHERE state IN ('queued','working') AND last_image_at<?", (stale,))
        if items:
            log.warning("worker did not answer for %s items; using mock", len(items))
    for item in items:
        db.ex("UPDATE mail_item SET state='analyzing' WHERE id=?", (item["id"],))
        hint = json.loads(item["raw_json"] or "{}").get("hint")
        finalize(item["id"], analyzer.analyze(_images(item["id"]), hint))
    return len(items)


def claim_jobs(limit: int = 3) -> list[dict]:
    """Worker pulls queued mail (images as base64)."""
    import base64

    jobs = []
    for item in db.q("SELECT * FROM mail_item WHERE state='queued' ORDER BY id LIMIT ?", (limit,)):
        db.ex("UPDATE mail_item SET state='working' WHERE id=?", (item["id"],))
        jobs.append({"id": item["id"], "images": [base64.b64encode(b).decode() for b in _images(item["id"])]})
    return jobs


def claim_line_work() -> dict:
    """Relay worker pulls outgoing LINE messages and image downloads."""
    outbox = db.q("SELECT * FROM line_outbox WHERE state='queued' ORDER BY id LIMIT 20")
    fetch = db.q("SELECT * FROM line_fetch WHERE state='queued' ORDER BY id LIMIT 10")
    for r in outbox:
        db.ex("UPDATE line_outbox SET state='sent' WHERE id=?", (r["id"],))
    for r in fetch:
        db.ex("UPDATE line_fetch SET state='working' WHERE id=?", (r["id"],))
    return {
        "outbox": [{"to": r["to_user"], "reply_token": r["reply_token"], "message": json.loads(r["message_json"])} for r in outbox],
        "fetch": [{"id": r["id"], "message_id": r["message_id"]} for r in fetch],
    }


def fetched_image(fetch_id: int, data: bytes) -> bool:
    r = db.one("SELECT * FROM line_fetch WHERE id=? AND state='working'", (fetch_id,))
    if not r:
        return False
    db.ex("UPDATE line_fetch SET state='done' WHERE id=?", (fetch_id,))
    handle_image(r["user_id"], data)
    return True


def notify_item(mail_id: int):
    m = db.one("SELECT i.*, f.parent_name FROM mail_item i JOIN family f ON f.id=i.family_id WHERE i.id=?", (mail_id,))
    if m["importance"] == "捨ててよい":
        return  # goes to the daily digest; never silently dropped
    text = messages.mail_card(m, m["parent_name"])
    for uid in recipients(m["family_id"]):
        line.push(uid, text, messages.status_buttons(mail_id))


def send_trash_digest() -> int:
    sent = 0
    for f in db.q("SELECT * FROM family"):
        items = db.q("SELECT * FROM mail_item WHERE family_id=? AND state='analyzed' AND importance='捨ててよい' AND trash_notified=0", (f["id"],))
        if not items:
            continue
        text = messages.trash_digest(f["parent_name"], items)
        buttons = [{"label": f"残す: {i['sender'][:12]}", "data": f"status={i['id']}:あとで"} for i in items]
        for uid in recipients(f["id"]):
            line.push(uid, text, buttons)
        db.ex(f"UPDATE mail_item SET trash_notified=1 WHERE id IN ({','.join(str(i['id']) for i in items)})")
        sent += len(items)
    return sent


def send_reminders(today: date | None = None) -> int:
    today = today or db.now().date()
    sent = 0
    rows = db.q("SELECT i.*, f.parent_name FROM mail_item i JOIN family f ON f.id=i.family_id WHERE i.state='analyzed' AND i.due_date IS NOT NULL AND i.status IN ('未対応','あとで')")
    for m in rows:
        days = (date.fromisoformat(m["due_date"]) - today).days
        col = "reminded_1" if days <= 1 else "reminded_7" if days <= 7 else None
        if col is None or days < 0 or m[col]:
            continue
        for uid in recipients(m["family_id"]):
            line.push(uid, messages.reminder(m, m["parent_name"], days), messages.status_buttons(m["id"]))
        db.ex(f"UPDATE mail_item SET {col}=1{', reminded_7=1' if col == 'reminded_1' else ''} WHERE id=?", (m["id"],))
        sent += 1
    return sent


def run_scheduled(now: datetime | None = None):
    """Called every few seconds. Batches always; daily jobs once per day after their hour."""
    now = now or db.now()
    process_due_batches()
    for name, hour, fn in (("trash", config.TRASH_DIGEST_HOUR, send_trash_digest), ("remind", config.REMINDER_HOUR, send_reminders)):
        today = now.date().isoformat()
        last = db.one("SELECT last_date FROM job_run WHERE name=?", (name,))
        if now.hour >= hour and (not last or last["last_date"] != today):
            fn()
            db.ex("INSERT OR REPLACE INTO job_run (name, last_date) VALUES (?,?)", (name, today))
