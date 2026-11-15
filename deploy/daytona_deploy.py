"""Deploy / update the app in a Daytona sandbox and print its URLs.

  python deploy/daytona_deploy.py           # create or update (data/ is kept)
  python deploy/daytona_deploy.py --logs    # show server logs
  python deploy/daytona_deploy.py --delete  # delete the sandbox (and its data)
"""
import os
import secrets
import sys
import time
from pathlib import Path

from daytona import CreateSandboxFromSnapshotParams, Daytona, SessionExecuteRequest
from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parent.parent
LABEL = ("app", "mail-mimamori")
PORT = 8000
REMOTE = "/home/daytona/mail"
SESSION = "server"

load_dotenv(ROOT / ".env")
daytona = Daytona()


def find():
    for sb in daytona.list():
        if (sb.labels or {}).get(LABEL[0]) == LABEL[1]:
            return sb
    return None


def logs(sb):
    try:
        s = sb.process.get_session(SESSION)
        out = sb.process.get_session_command_logs(SESSION, s.commands[-1].id)
        print((out.output or "")[-6000:])
    except Exception as e:
        print(f"no logs: {e}")


def ensure_worker_token() -> str:
    tok = os.getenv("WORKER_TOKEN")
    if not tok:
        tok = secrets.token_urlsafe(24)
        with open(ROOT / ".env", "a", encoding="utf-8") as f:
            f.write(f"\nWORKER_TOKEN={tok}\n")
        os.environ["WORKER_TOKEN"] = tok
    return tok


def upload(sb, base_url: str):
    files = [p for p in (ROOT / "app").rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    files.append(ROOT / "requirements.txt")
    dirs = sorted({str(Path(REMOTE) / p.relative_to(ROOT).parent).replace("\\", "/") for p in files})
    sb.process.exec("mkdir -p " + " ".join(dirs) + f" {REMOTE}/data")
    for p in files:
        sb.fs.upload_file(p.read_bytes(), f"{REMOTE}/{p.relative_to(ROOT).as_posix()}")
    # .env for the app: never ship Daytona credentials into the sandbox
    env = {k: v for k, v in dotenv_values(ROOT / ".env").items() if v and not k.startswith("DAYTONA_")}
    env["BASE_URL"] = base_url
    # Daytona tier network limits may block outbound AI APIs; fall back to the mock analyzer.
    for key, url in (("NOSANA_API_KEY", "https://inference.nosana.com/v1/models"), ("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/models")):
        if key in env:
            code = (sb.process.exec(f"curl -s -m 8 -o /dev/null -w '%{{http_code}}' {url}").result or "").strip()
            if code in ("", "000"):
                print(f"WARNING: sandbox cannot reach {url}; {key} disabled (network limit)")
                env.pop(key)
    if not any(k in env for k in ("NOSANA_API_KEY", "ANTHROPIC_API_KEY")):
        if os.getenv("NOSANA_API_KEY"):
            # Relay mode: deploy/nosana_worker.py pulls jobs from outside and calls Nosana.
            env["ANALYZER"] = "remote"
            env["WORKER_TOKEN"] = ensure_worker_token()
            print("-> using relay: run  python deploy/nosana_worker.py  to read mail with Nosana")
        else:
            env["ANALYZER"] = "fake"
    print(f"analyzer: {env.get('ANALYZER') or ('nosana' if 'NOSANA_API_KEY' in env else 'claude')}")
    # Same for LINE: if the sandbox cannot reach the LINE API, the worker sends messages and
    # downloads photos. The access token then stays on this machine; only the secret
    # (webhook signature check) goes into the sandbox.
    if "LINE_CHANNEL_ACCESS_TOKEN" in env:
        code = (sb.process.exec("curl -s -m 8 -o /dev/null -w '%{http_code}' https://api.line.me/v2/bot/info").result or "").strip()
        if code in ("", "000"):
            env.pop("LINE_CHANNEL_ACCESS_TOKEN")
            env["LINE_MODE"] = "relay"
            env["WORKER_TOKEN"] = ensure_worker_token()
            print("-> LINE via relay: keep  python deploy/nosana_worker.py  running")
    print(f"line: {env.get('LINE_MODE') or ('real' if 'LINE_CHANNEL_ACCESS_TOKEN' in env else 'fake')}")
    env["DATA_DIR"] = f"{REMOTE}/data"
    sb.fs.upload_file("\n".join(f"{k}={v}" for k, v in env.items()).encode(), f"{REMOTE}/.env")


def start(sb):
    try:
        sb.process.delete_session(SESSION)
    except Exception:
        pass
    sb.process.exec("pkill -f 'uvicorn app.main:app' || true")
    sb.process.create_session(SESSION)
    sb.process.execute_session_command(
        SESSION,
        SessionExecuteRequest(command=f"cd {REMOTE} && python -m uvicorn app.main:app --host 0.0.0.0 --port {PORT}", run_async=True),
    )
    for _ in range(40):
        r = sb.process.exec(f"curl -s localhost:{PORT}/health")
        if '"ok"' in (r.result or ""):
            return True
        time.sleep(1)
    return False


def main():
    sb = find()
    if "--delete" in sys.argv:
        if sb:
            daytona.delete(sb)
            print(f"deleted {sb.id}")
        return
    if "--logs" in sys.argv:
        return logs(sb) if sb else print("no sandbox")

    if sb is None:
        sb = daytona.create(CreateSandboxFromSnapshotParams(
            language="python", labels=dict([LABEL]), public=True, auto_stop_interval=0,
        ))
        print(f"created sandbox {sb.id}")
    else:
        if sb.state != "started":
            sb.start()
        print(f"updating sandbox {sb.id}")

    base_url = sb.get_preview_link(PORT).url.rstrip("/")
    upload(sb, base_url)
    r = sb.process.exec(f"pip install -q -r {REMOTE}/requirements.txt", timeout=600)
    if r.exit_code != 0:
        sys.exit(f"pip install failed:\n{r.result[-2000:]}")
    if not start(sb):
        logs(sb)
        sys.exit("server did not start")

    print(f"\nDemo URL:    {base_url}/demo")
    print(f"Webhook URL: {base_url}/callback")
    print(f"Health:      {base_url}/health")


if __name__ == "__main__":
    main()
