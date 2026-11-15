"""End-to-end tests with fake LINE and fake AI."""
import base64
import hashlib
import hmac
import json
from datetime import timedelta

import pytest


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LINE_MODE", "fake")
    monkeypatch.setenv("ANALYZER", "fake")
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "")
    import importlib

    from app import analyzer, config, db, line, messages, service
    importlib.reload(config)
    db.reset_for_tests()
    for m in (db, line, analyzer, messages, service):
        importlib.reload(m)
    yield service, db, config
    db.reset_for_tests()


def inbox(db, uid):
    return [r["text"] for r in db.q("SELECT text FROM notification_log WHERE to_user=? ORDER BY id", (uid,))]


def setup_family(service, db):
    service.handle_text("child", "登録 母")
    f = db.one("SELECT * FROM family")
    service.handle_text("helper", f"参加 {f['photographer_code']}")
    service.handle_text("sib", f"参加 {f['family_code']}")
    return f


def test_register_and_join(app_env):
    service, db, _ = app_env
    f = setup_family(service, db)
    assert f["photographer_code"] in inbox(db, "child")[0]
    roles = {r["line_user_id"]: r["role"] for r in db.q("SELECT * FROM member")}
    assert roles == {"child": "owner", "helper": "photographer", "sib": "family"}


def test_photos_batched_and_notified(app_env):
    service, db, _ = app_env
    setup_family(service, db)
    service.handle_image("helper", b"img1", hint=0)
    service.handle_image("helper", b"img2", hint=0)
    assert len(db.q("SELECT * FROM mail_item")) == 1
    assert len(db.q("SELECT * FROM mail_image")) == 2
    assert service.process_due_batches(force=True) == 1
    m = db.one("SELECT * FROM mail_item")
    assert m["importance"] == "要対応"
    assert "要対応" in inbox(db, "child")[-1]
    assert "要対応" in inbox(db, "sib")[-1]
    assert not any("要対応" in t for t in inbox(db, "helper"))


def test_trash_goes_to_digest_not_dropped(app_env):
    service, db, _ = app_env
    setup_family(service, db)
    service.handle_image("helper", b"dm", hint=3)
    service.process_due_batches(force=True)
    assert db.one("SELECT importance FROM mail_item")["importance"] == "捨ててよい"
    before = len(inbox(db, "child"))
    assert service.send_trash_digest() == 1
    assert "捨ててよい" in inbox(db, "child")[before]
    assert service.send_trash_digest() == 0


def test_safety_correction():
    from app.analyzer import correct
    r = correct(dict(sender="市役所", kind="DM", summary="", amount=None, due_date=None, action="", importance="捨ててよい", confidence=0.99))
    assert r["importance"] == "確認"
    r = correct(dict(sender="通販", kind="DM", summary="", amount=None, due_date=None, action="", importance="捨ててよい", confidence=0.3))
    assert r["importance"] == "確認"
    r = correct(dict(sender="x", kind="x", summary="", amount=None, due_date=None, action="", importance="???", confidence=1))
    assert r["importance"] == "確認"


def test_postback_and_reminder(app_env):
    service, db, _ = app_env
    setup_family(service, db)
    service.handle_image("helper", b"x", hint=1)  # due in 5 days
    service.process_due_batches(force=True)
    m = db.one("SELECT * FROM mail_item")
    assert service.send_reminders() == 1
    assert service.send_reminders() == 0
    due = db.now().date() + timedelta(days=5)
    assert service.send_reminders(due - timedelta(days=1)) == 1
    service.handle_postback("sib", f"status={m['id']}:対応済み")
    assert db.one("SELECT status FROM mail_item")["status"] == "対応済み"
    assert "対応済み" in inbox(db, "child")[-1]


def test_webhook_signature(app_env, monkeypatch):
    service, db, config = app_env
    from fastapi.testclient import TestClient

    from app import main
    monkeypatch.setattr(config, "LINE_CHANNEL_SECRET", "s3cret")
    body = json.dumps({"events": [{"type": "message", "replyToken": "r", "source": {"userId": "child"}, "message": {"type": "text", "text": "登録 父"}}]}).encode()
    sig = base64.b64encode(hmac.new(b"s3cret", body, hashlib.sha256).digest()).decode()
    with TestClient(main.app) as c:
        assert c.post("/callback", content=body, headers={"x-line-signature": "bad"}).status_code == 400
        assert c.post("/callback", content=body, headers={"x-line-signature": sig}).status_code == 200
    assert db.one("SELECT parent_name FROM family")["parent_name"] == "父"


def test_remote_worker_and_fallback(app_env, monkeypatch):
    service, db, config = app_env
    monkeypatch.setattr(config, "ANALYZER", "remote")
    setup_family(service, db)
    service.handle_image("helper", b"x", hint=0)
    service.process_due_batches(force=True)
    assert db.one("SELECT state FROM mail_item")["state"] == "queued"
    jobs = service.claim_jobs()
    assert len(jobs) == 1 and jobs[0]["images"]
    assert service.claim_jobs() == []
    r = dict(sender="市役所", kind="納付書", summary="s", amount=1000, due_date=None, action="払う", importance="捨ててよい", confidence=0.9)
    assert service.finalize(jobs[0]["id"], r)
    assert db.one("SELECT importance FROM mail_item")["importance"] == "確認"  # corrected
    assert not service.finalize(jobs[0]["id"], r)  # idempotent
    # worker never answers -> mock fallback after timeout
    service.handle_image("helper", b"y", hint=3)
    service.process_due_batches(force=True)
    monkeypatch.setattr(config, "REMOTE_TIMEOUT_SEC", -1000)
    monkeypatch.setattr(config, "BATCH_WINDOW_SEC", 0)
    assert service.process_due_batches() == 1
    assert db.q("SELECT state FROM mail_item WHERE state!='analyzed'") == []


def test_line_relay(app_env, monkeypatch):
    service, db, config = app_env
    from fastapi.testclient import TestClient

    from app import line, main
    monkeypatch.setattr(config, "LINE_MODE", "relay")
    monkeypatch.setattr(config, "WORKER_TOKEN", "tok")
    child, helper = "U" + "a" * 32, "U" + "b" * 32
    h = {"authorization": "Bearer tok"}

    monkeypatch.setattr(config, "LINE_CHANNEL_SECRET", "s3cret")

    def hook(c, ev):
        body = json.dumps({"events": [ev]}).encode()
        sig = base64.b64encode(hmac.new(b"s3cret", body, hashlib.sha256).digest()).decode()
        return c.post("/callback", content=body, headers={"x-line-signature": sig})

    with TestClient(main.app) as c:
        assert c.get("/worker/jobs").status_code == 401
        hook(c, {"type": "message", "replyToken": "rt1", "source": {"userId": child}, "message": {"type": "text", "text": "登録 母"}})
        f = db.one("SELECT * FROM family")
        hook(c, {"type": "message", "replyToken": "rt2", "source": {"userId": helper}, "message": {"type": "text", "text": f"参加 {f['photographer_code']}"}})
        work = c.get("/worker/jobs", headers=h).json()
        assert [m["reply_token"] for m in work["outbox"]] == ["rt1", "rt2"]
        assert work["outbox"][0]["message"]["type"] == "text"
        assert c.get("/worker/jobs", headers=h).json()["outbox"] == []  # claimed once
        # photo: queued for download, then delivered by the worker
        hook(c, {"type": "message", "replyToken": "rt3", "source": {"userId": helper}, "message": {"type": "image", "id": "m1"}})
        work = c.get("/worker/jobs", headers=h).json()
        assert work["fetch"][0]["message_id"] == "m1"
        assert c.post(f"/worker/image/{work['fetch'][0]['id']}", headers=h, content=b"jpegbytes").json()["ok"]
        assert len(db.q("SELECT * FROM mail_image")) == 1
        service.process_due_batches(force=True)
        out = c.get("/worker/jobs", headers=h).json()["outbox"]
        card = [m for m in out if m["to"] == child][-1]["message"]
        assert card["type"] == "flex" and len(card["contents"]["footer"]["contents"]) == 3
    # demo users never reach LINE
    line.push("U_child", "x")
    assert db.q("SELECT * FROM line_outbox WHERE to_user='U_child'") == []
