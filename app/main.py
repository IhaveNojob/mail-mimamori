"""Minimal skeleton: user prompt -> (LLM writes code) -> run it in a Daytona sandbox -> return result.

Run:  uvicorn app.main:app --reload
Test: curl -X POST localhost:8000/run -H 'content-type: application/json' \
        -d '{"code": "print(1+1)"}'
"""
from daytona import Daytona
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel

load_dotenv()  # reads DAYTONA_API_KEY (and your LLM key) from .env

app = FastAPI(title="Daytona HackSprint")
daytona = Daytona()  # picks up DAYTONA_API_KEY from the environment


class RunRequest(BaseModel):
    code: str


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/run")
def run(req: RunRequest):
    """Execute Python code inside a fresh, isolated Daytona sandbox."""
    sandbox = daytona.create()
    try:
        resp = sandbox.process.code_run(req.code)
        return {"exit_code": resp.exit_code, "output": resp.result}
    finally:
        daytona.delete(sandbox)  # always clean up so credits aren't wasted
