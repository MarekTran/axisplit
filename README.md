# axisplit

Splitwise/Tricount for group receipts with receipt-OCR at the core: photograph a
check, OCR detects line items as bounding boxes, and each person "claims" items
by dropping their colored token onto them. The split is derived from tokens;
everyone confirms over a shareable link.

Read [`docs/spec.md`](docs/spec.md) for the domain model, flows, realtime
design, OCR plan, and stack decisions.

## Status

v0.1 prototype skeleton: SSR FastAPI + Jinja + htmx/Alpine app (OCR stubbed,
manual item entry works end-to-end), plus a `bench/` harness to pick the real
OCR model (dots.ocr vs Qwen3-VL vs DeepSeek-OCR-2 vs Nanonets vs ...).

## Dev quickstart (NixOS or any Nix flake machine)

Everything runs inside the pinned flake dev shell — do **not** use your ambient
Python (the flake pins python312 + postgres; `uv sync` inside it builds the venv
from the lockfile):

```bash
direnv allow            # auto-enter shell, or manually: nix develop
uv sync --all-extras    # deps into .venv
uv run uvicorn axisplit.main:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 — claim a username, create a tab, upload a receipt
photo, add items manually (until the OCR benchmark lands), place tokens,
confirm. The tab page shows a shareable invite link; open it from your phone
on the LAN to see live token placement across devices (WebSocket room).

Dev DB is SQLite under `./data/`. Postgres (still inside `nix develop`):

```bash
nix build .#postgres-init && ./result/bin/pg-dev   # starts local server on :5433
DATABASE_URL='postgresql+asyncpg://'$USER'@localhost:5433/axisplit' \
  uv run uvicorn axisplit.main:app --reload
```

Regression tests (both modes verified):

```bash
bash scripts/smoke.sh          # full HTTP golden path: Adam+Nina split
uv run python scripts/ws_smoke.py   # live WebSocket broadcast test
```

## OCR benchmark

```bash
uv run python -m bench.run --model stub --out bench/preds/stub     # plumbing smoke test
uv run python -m bench/score.py --gt bench/cases --preds bench/preds/stub
```

Put real receipt photos + `gt.json` in `bench/cases/<id>/` (photos are
gitignored). See `bench/README.md` for candidate models and env vars.
