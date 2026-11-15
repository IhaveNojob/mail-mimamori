"""Notification texts."""
from . import config

ICON = {"要対応": "🔴", "確認": "🟡", "捨ててよい": "⚪"}


def yen(n) -> str:
    return f"{n:,}円" if n else "-"


def mail_card(m, parent_name: str) -> str:
    if m["scam_risk"]:
        head = [f"⚠️【詐欺の疑い】{parent_name}さん宛ての郵便", f"注意: {m['scam_reason']}"]
    else:
        head = [f"{ICON.get(m['importance'], '')}【{m['importance']}】{parent_name}さん宛ての郵便"]
    lines = [
        *head,
        f"差出人: {m['sender'] or '不明'}",
        f"種類: {m['kind'] or '-'}",
        f"内容: {m['summary'] or '-'}",
    ]
    if m["amount"]:
        lines.append(f"金額: {yen(m['amount'])}")
    if m["due_date"]:
        lines.append(f"期限: {m['due_date']}")
    if m["action"] and m["action"] != "なし":
        lines.append(f"やること: {m['action']}")
    return "\n".join(lines)


def status_buttons(mail_id: int) -> list[dict]:
    return [
        {"label": "対応済み", "data": f"status={mail_id}:対応済み"},
        {"label": "あとで", "data": f"status={mail_id}:あとで"},
        {"label": "不要", "data": f"status={mail_id}:不要"},
    ]


def history_url(token: str) -> str:
    return f"{config.BASE_URL}/history/{token}"


def registered(parent_name: str, photographer_code: str, family_code: str) -> str:
    return (
        f"「{parent_name}さん」の見守りを登録しました。\n\n"
        f"📷 撮影する人（現地の方）に送ってもらう言葉:\n参加 {photographer_code}\n\n"
        f"👪 通知を受け取る家族に送ってもらう言葉:\n参加 {family_code}"
    )


def trash_digest(parent_name: str, items) -> str:
    body = "\n".join(f"・{m['sender'] or '不明'}（{m['kind'] or '-'}）{m['summary'] or ''}" for m in items)
    return f"⚪ 今日届いた「捨ててよい」郵便（{parent_name}さん宛て）{len(items)}通\n{body}\n\n残したいものがあれば「あとで」を押してください。"


def reminder(m, parent_name: str, days_left: int) -> str:
    when = "明日" if days_left <= 1 else f"あと{days_left}日"
    return f"⏰ 期限が{when}です\n" + mail_card(m, parent_name)


HELP = (
    "使い方:\n"
    "・登録 母 … 見守りを始める（招待コードが届きます）\n"
    "・参加 コード … 撮影者／家族として参加\n"
    "・写真を送る … 郵便物を読み取って家族に通知\n"
    "・一覧 … 未対応の郵便と履歴ページ"
)
