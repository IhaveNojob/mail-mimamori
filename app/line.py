"""LINE signature check and send/receive.

LINE_MODE:
  fake  - nothing leaves the process; messages are only recorded in notification_log (/demo)
  real  - call the LINE API directly
  relay - this host cannot reach the LINE API (Daytona network limits); outgoing messages and
          image downloads are queued and carried out by deploy/nosana_worker.py
"""
import base64
import hashlib
import hmac
import json
import logging
import re

import httpx

from . import config, db

log = logging.getLogger(__name__)
API = "https://api.line.me/v2/bot"
DATA_API = "https://api-data.line.me/v2/bot"
REAL_USER = re.compile(r"U[0-9a-f]{32}")  # demo users (U_child ...) never go to LINE


def verify_signature(body: bytes, signature: str) -> bool:
    if not config.LINE_CHANNEL_SECRET:
        return config.LINE_MODE == "fake"
    mac = hmac.new(config.LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(), signature or "")


def _headers():
    return {"Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"}


def to_line_message(text: str, buttons: list | None) -> dict:
    """Plain text, or a Flex bubble with postback buttons ([{"label", "data"}])."""
    if not buttons:
        return {"type": "text", "text": text[:5000]}
    return {
        "type": "flex",
        "altText": text[:400],
        "contents": {
            "type": "bubble",
            "body": {"type": "box", "layout": "vertical", "contents": [{"type": "text", "text": text[:2000], "wrap": True, "size": "sm"}]},
            "footer": {
                "type": "box", "layout": "vertical", "spacing": "sm",
                "contents": [
                    {"type": "button", "height": "sm", "style": "primary" if i == 0 else "secondary",
                     "action": {"type": "postback", "label": b["label"][:20], "data": b["data"], "displayText": b["label"][:300]}}
                    for i, b in enumerate(buttons[:6])
                ],
            },
        },
    }


def send_direct(to_user: str, reply_token: str | None, message: dict):
    """Reply (free) when a token is available, otherwise push. Used in real mode and by the relay worker."""
    if reply_token:
        r = httpx.post(f"{API}/message/reply", headers=_headers(), json={"replyToken": reply_token, "messages": [message]}, timeout=10)
        if r.status_code == 200:
            return
        log.warning("LINE reply failed (%s); falling back to push", r.status_code)
    r = httpx.post(f"{API}/message/push", headers=_headers(), json={"to": to_user, "messages": [message]}, timeout=10)
    if r.status_code != 200:
        log.error("LINE push failed: %s %s", r.status_code, r.text[:300])


def reply(reply_token: str | None, to_user: str, text: str, buttons: list | None = None):
    db.log_notification(to_user, text, buttons)
    if config.LINE_MODE == "fake" or not REAL_USER.fullmatch(to_user):
        return
    message = to_line_message(text, buttons)
    if config.LINE_MODE == "relay":
        db.ex("INSERT INTO line_outbox (to_user, reply_token, message_json, state, created_at) VALUES (?,?,?,?,?)",
              (to_user, reply_token, json.dumps(message, ensure_ascii=False), "queued", db.now_iso()))
        return
    try:
        send_direct(to_user, reply_token, message)
    except Exception:
        log.exception("LINE send failed")


def push(to_user: str, text: str, buttons: list | None = None):
    reply(None, to_user, text, buttons)


def get_content(message_id: str) -> bytes:
    r = httpx.get(f"{DATA_API}/message/{message_id}/content", headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.content
