"""Read mail photos with AI, then apply safety-side corrections.

ANALYZER=fake returns canned results (deterministic per image) so the demo runs without keys.
"""
import base64
import hashlib
import json
import logging
from datetime import date, timedelta

from . import config, db

log = logging.getLogger(__name__)

IMPORTANCE = ("要対応", "確認", "捨ててよい")
FIELDS = ("sender", "kind", "summary", "amount", "due_date", "action", "importance", "confidence", "scam_risk", "scam_reason")
SCAM_ACTION = "支払いも電話もせず、家族で確認する（迷ったら消費者ホットライン188）"

SCHEMA = {
    "type": "object",
    "properties": {
        "sender": {"type": "string", "description": "差出人"},
        "kind": {"type": "string", "description": "種類 (請求書, 納付書, お知らせ, DM など)"},
        "summary": {"type": "string", "description": "60字以内の要約"},
        "amount": {"type": ["integer", "null"], "description": "支払金額(円)。なければ null"},
        "due_date": {"type": ["string", "null"], "description": "期限 YYYY-MM-DD。なければ null"},
        "action": {"type": "string", "description": "家族がやること。なければ「なし」"},
        "importance": {"type": "string", "enum": list(IMPORTANCE)},
        "confidence": {"type": "number", "description": "読み取りの自信 0〜1"},
        "scam_risk": {"type": "boolean", "description": "架空請求・詐欺・悪質商法の疑いがあれば true"},
        "scam_reason": {"type": ["string", "null"], "description": "疑う理由を40字以内で。疑いがなければ null"},
    },
    "required": list(FIELDS),
    "additionalProperties": False,
}

SYSTEM = """あなたは高齢の親に届いた郵便物を、離れて暮らす家族のために仕分けるアシスタントです。
写真(複数枚は同じ1通)から差出人・種類・要約・金額・期限・やること・重要度を読み取ってください。
重要度: 要対応=支払い・手続き・返送など期限のある行動が必要 / 確認=家族が目を通すべき / 捨ててよい=広告・DMなど。
迷ったら捨ててよいにせず確認にしてください。読み取れない項目は推測せず null にしてください。
架空請求や詐欺の疑いも見てください。身に覚えのない未納料金、「訴訟」「最終通告」「差し押さえ」で不安をあおる、本日中の電話を迫る、
プリペイドカードや個人名義の口座で払わせる、実在しない機関名、といった特徴があれば scam_risk を true にして理由を書きます。
役所・電力・病院などの通常の請求書、納付書、お知らせは false です。
LINEで読むので、要約は60字以内、やることは40字以内で簡潔に。宛名の人の名前は書かないでください。"""


def _fake_samples() -> list[dict]:
    t = db.now().date()
    return [
        dict(sender="東京電力エナジーパートナー", kind="請求書", summary="11月分の電気料金のお知らせ", amount=12340,
             due_date=str(t + timedelta(days=10)), action="口座振替でなければコンビニで支払う", importance="要対応", confidence=0.93),
        dict(sender="○○市役所 介護保険課", kind="納付書", summary="介護保険料（第5期）の納付書", amount=8500,
             due_date=str(t + timedelta(days=5)), action="期限までに金融機関かコンビニで納付", importance="要対応", confidence=0.9),
        dict(sender="日本年金機構", kind="お知らせ", summary="ねんきん定期便。加入記録の確認依頼", amount=None,
             due_date=None, action="記録に誤りがないか確認", importance="確認", confidence=0.88),
        dict(sender="△△通販", kind="DM", summary="冬のセールカタログ", amount=None, due_date=None,
             action="なし", importance="捨ててよい", confidence=0.95),
        dict(sender="□□クリニック", kind="お知らせ", summary="健康診断の結果のお知らせ", amount=None, due_date=None,
             action="結果を確認し、再検査が必要なら予約", importance="確認", confidence=0.85),
        dict(sender="××不動産", kind="チラシ", summary="ご自宅の売却査定キャンペーン", amount=None, due_date=None,
             action="なし", importance="捨ててよい", confidence=0.9),
        dict(sender="民事訴訟管理センター", kind="ハガキ", summary="未納料金があるとして訴訟を予告し、電話を求める通知", amount=None,
             due_date=str(t + timedelta(days=2)), action="記載の番号に電話", importance="要対応", confidence=0.9,
             scam_risk=True, scam_reason="実在しない機関名で、訴訟をちらつかせ電話を急がせている"),
    ]


def analyze_fake(images: list[bytes], hint: int | None = None) -> dict:
    samples = _fake_samples()
    if hint is None:
        hint = int(hashlib.sha256(b"".join(images)).hexdigest(), 16)
    return dict(samples[hint % len(samples)])


def _media_type(b: bytes) -> str:
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    if b[:3] == b"GIF":
        return "image/gif"
    return "image/jpeg"


def analyze_claude(images: list[bytes]) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": _media_type(b), "data": base64.standard_b64encode(b).decode()}}
        for b in images[:10]
    ]
    content.append({"type": "text", "text": f"今日は {db.now().date()} です。この郵便物を読み取ってください。"})
    resp = client.beta.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=16000,
        system=SYSTEM,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": content}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model refused")
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


def _shrink(b: bytes, max_side: int = 1600) -> bytes:
    """Phone photos are 12MP+; cap the long side (smaller than this hurt reading accuracy)."""
    try:
        import io

        from PIL import Image, ImageOps

        im = Image.open(io.BytesIO(b))
        if max(im.size) <= max_side:
            return b
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((max_side, max_side))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=85)
        return out.getvalue()
    except Exception:
        return b


def analyze_nosana(images: list[bytes]) -> dict:
    """Nosana LLM inference (OpenAI-compatible, vLLM). Structured output via json_schema."""
    import httpx

    images = [_shrink(b) for b in images[:6]]
    content = [
        {"type": "image_url", "image_url": {"url": f"data:{_media_type(b)};base64,{base64.standard_b64encode(b).decode()}"}}
        for b in images
    ]
    content.append({"type": "text", "text": f"今日は {db.now().date()} です。この郵便物を読み取り、指定のJSONだけを返してください。"})
    body = {
        "model": config.NOSANA_MODEL,
        "max_tokens": 4000,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "mail", "schema": SCHEMA, "strict": True}},
        # Qwen "thinking" tripled latency (16s -> 5s) without changing results on our samples.
        "chat_template_kwargs": {"enable_thinking": config.NOSANA_THINKING},
    }
    last = None
    for _ in range(2):  # the shared GPU endpoint occasionally fails once; retry before giving up
        try:
            r = httpx.post(f"{config.NOSANA_BASE_URL}/chat/completions",
                           headers={"Authorization": f"Bearer {config.NOSANA_API_KEY}"}, json=body, timeout=60)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"] or ""
            start, end = text.find("{"), text.rfind("}")
            return json.loads(text[start : end + 1])
        except Exception as e:
            last = e
            log.warning("nosana request failed: %s", e)
    raise last


SAFE_KEYWORDS = ("役所", "市", "区", "町", "村", "税", "年金", "保険", "督促", "催告", "裁判", "差押", "請求", "納付", "更新", "期限")


def correct(r: dict) -> dict:
    """Never let something possibly important be silently marked as trash."""
    r = {k: r.get(k) for k in FIELDS}
    r["scam_risk"] = bool(r["scam_risk"])
    if r["scam_risk"]:
        # Tell the family now, and never nag them to "pay before the deadline".
        r.update(importance="要対応", action=SCAM_ACTION, due_date=None, scam_reason=r["scam_reason"] or "架空請求の特徴があります")
        return r
    r["scam_reason"] = None
    if r["importance"] not in IMPORTANCE:
        r["importance"] = "確認"
    text = " ".join(str(r.get(k) or "") for k in ("sender", "kind", "summary", "action"))
    if r["importance"] == "捨ててよい" and (
        r["amount"] or r["due_date"] or any(k in text for k in SAFE_KEYWORDS) or (r["confidence"] or 0) < 0.7
    ):
        r["importance"] = "確認"
    if r["due_date"]:
        try:
            due = date.fromisoformat(r["due_date"])
            if r["amount"] and due - db.now().date() <= timedelta(days=14):
                r["importance"] = "要対応"
        except ValueError:
            r["due_date"] = None
    return r


def analyze(images: list[bytes], hint: int | None = None) -> dict:
    try:
        if config.ANALYZER == "nosana":
            raw = analyze_nosana(images)
        elif config.ANALYZER == "claude":
            raw = analyze_claude(images)
        else:
            raw = analyze_fake(images, hint)
    except Exception:
        log.exception("analyze failed; falling back to 確認")
        raw = FAILED
    return correct(raw)


FAILED = dict(sender="不明", kind="不明", summary="自動で読み取れませんでした。写真を確認してください。",
              amount=None, due_date=None, action="写真を見て判断", importance="確認", confidence=0,
              scam_risk=False, scam_reason=None)
