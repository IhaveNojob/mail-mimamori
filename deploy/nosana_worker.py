"""Relay between the Daytona-hosted app and services its sandbox cannot reach.

Daytona tier 1/2 sandboxes can only call an allow list of hosts (not inference.nosana.com,
not api.line.me), but inbound requests to the sandbox's preview URL work. This worker runs on
any machine with internet access and, every second:
  - sends queued LINE messages (reply / push)
  - downloads photos users sent on LINE and hands them to the app
  - reads queued mail with Nosana and posts the result back

  python deploy/nosana_worker.py                 # finds the sandbox URL via the Daytona SDK
  python deploy/nosana_worker.py https://8000-xxxx.daytonaproxy01.net
"""
import sys
import time
from base64 import b64decode
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "deploy"))
load_dotenv(ROOT / ".env")

from app import analyzer, config, line  # noqa: E402


def find_url() -> str:
    from daytona import Daytona

    from daytona_deploy import LABEL, PORT

    for sb in Daytona().list():
        if (sb.labels or {}).get(LABEL[0]) == LABEL[1]:
            return sb.get_preview_link(PORT).url.rstrip("/")
    sys.exit("sandbox not found; run deploy/daytona_deploy.py first")


def read_mail(url: str, h: dict, job: dict):
    t = time.time()
    try:
        r = analyzer.analyze_nosana([b64decode(x) for x in job["images"]])
    except Exception as e:
        r = {"error": str(e)}
    httpx.post(f"{url}/worker/result/{job['id']}", headers=h, json=r, timeout=30)
    print(f"mail {job['id']}: {r.get('importance', 'ERROR')} {r.get('sender', r.get('error', ''))} ({time.time() - t:.1f}s)")


def main():
    if not config.WORKER_TOKEN:
        sys.exit("WORKER_TOKEN is not set in .env; run deploy/daytona_deploy.py first")
    url = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else find_url()
    h = {"Authorization": f"Bearer {config.WORKER_TOKEN}"}
    print(f"worker: {url}")
    print(f"  nosana: {config.NOSANA_MODEL if config.NOSANA_API_KEY else 'no key'}   line: {'on' if config.LINE_CHANNEL_ACCESS_TOKEN else 'no token'}   (Ctrl+C to stop)")
    pool = ThreadPoolExecutor(max_workers=3)  # reading takes seconds; keep LINE replies snappy meanwhile
    with httpx.Client(timeout=30) as c:
        while True:
            try:
                work = c.get(f"{url}/worker/jobs", headers=h).raise_for_status().json()
            except Exception as e:
                print(f"poll failed: {e}")
                time.sleep(5)
                continue
            for m in work.get("outbox", []):
                try:
                    line.send_direct(m["to"], m["reply_token"], m["message"])
                    print(f"line -> {m['to'][:8]}… {m['message'].get('text', m['message'].get('altText', ''))[:30]!r}")
                except Exception as e:
                    print(f"line send failed: {e}")
            for f in work.get("fetch", []):
                try:
                    data = line.get_content(f["message_id"])
                    c.post(f"{url}/worker/image/{f['id']}", headers=h, content=data)
                    print(f"photo {f['id']}: {len(data) // 1024} KB")
                except Exception as e:
                    print(f"photo fetch failed: {e}")
            for job in work.get("jobs", []):
                pool.submit(read_mail, url, h, job)
            time.sleep(1)


if __name__ == "__main__":
    main()
