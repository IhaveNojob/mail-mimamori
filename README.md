# Daytona HackSprint Tokyo (2026-11-15)

AI app prototype built on [Daytona](https://www.daytona.io) sandboxes.

Flow: user request -> LLM writes code -> runs in an isolated Daytona sandbox -> result shown in UI.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DAYTONA_API_KEY at https://app.daytona.io
uvicorn app.main:app --reload
```

## Schedule
- 15:00-17:00 build, 17:20 demo (2 min), 18:30 results

Judging: completeness > novelty > real-world impact > use of sponsor products (Daytona, Nosana).
