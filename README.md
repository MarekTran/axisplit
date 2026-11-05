# axisplit

Splitwise/Tricount for group receipts with receipt-OCR at the core: photograph a
check, OCR detects line items as bounding boxes, and each person "claims" items
by dropping their colored token onto them. The split is derived from tokens;
everyone confirms over a shareable link.

Read [`docs/spec.md`](docs/spec.md) for the domain model, flows, realtime
design, OCR plan, and stack decisions. [`docs/ux.md`](docs/ux.md) describes the
interaction design, duplicate recovery and accessibility decisions.

## Status

SSR FastAPI + Jinja app with automatic local vision extraction on upload.
Qwen3-VL-4B runs in llama.cpp (Vulkan on Linux/AMD); it returns item names,
line-total prices and boxes. Model predictions need checking: JSON validation
ensures sane geometry, not that the model read every price or box correctly.

## Dev quickstart (NixOS or any Nix flake machine)

Everything runs inside the pinned flake dev shell — do **not** use your ambient
Python (the flake pins python312 + postgres; `uv sync` inside it builds the venv
from the lockfile):

```bash
direnv allow            # auto-enter shell, or manually: nix develop
uv sync --all-extras    # deps into .venv
uv run uvicorn axisplit.main:app --reload --host 0.0.0.0 --port 8000
```

In a **second dev-shell terminal**, start the vision server first:

```bash
nix develop
axisplit-ocr
# Wait for "model loaded"; first launch downloads ~3 GB into ~/.cache/axisplit-models.
```

Open http://localhost:8000 — upload a receipt and wait for automatic detection
(up to five minutes). Names, line-total prices and boxes are saved together;
then place tokens and confirm. Existing receipts with no items have a
**Detect items automatically** button; no re-upload required. Failed inference
is shown explicitly and can be retried, never silently treated as successful OCR.

For GLM-OCR experiments (stop the Qwen server first):

```bash
AXISPLIT_OCR_HF=ggml-org/GLM-OCR-GGUF axisplit-ocr
```

GLM's documented layout pipeline uses a separate detector; the current app's
prompt requests grounding boxes directly and is tested with Qwen, not GLM.
Mistral OCR is a hosted service, not a downloadable llama.cpp model. InternVL
and Gemma are alternatives to benchmark, not drop-in guarantees of accurate boxes.
`AXISPLIT_OCR_URL` overrides the default `http://127.0.0.1:8081/v1`.

The tab page provides a shareable invite link for collaboration on your LAN.

Dev DB is SQLite under `./data/`. Postgres (still inside `nix develop`):

```bash
nix build .#postgres-init && ./result/bin/pg-dev   # starts local server on :5433
DATABASE_URL='postgresql+asyncpg://'$USER'@localhost:5433/axisplit' \
  uv run uvicorn axisplit.main:app --reload
```

Regression tests (both modes verified):

```bash
uv run pytest -q tests/test_ocr.py tests/test_ux.py    # OCR, safe recovery, constraints
nix shell --inputs-from . nixpkgs#nodejs -c node --test tests/hit-test.test.cjs
nix shell --inputs-from . nixpkgs#geckodriver -c \
  uv run --with selenium python scripts/ux_browser.py   # isolated browser tests
# UI-only tests must explicitly opt out of real inference:
AXISPLIT_OCR_BACKEND=stub bash scripts/smoke.sh
AXISPLIT_OCR_BACKEND=stub uv run python scripts/ws_smoke.py
```

## OCR benchmark

```bash
uv run python -m bench.run --model stub --out bench/preds/stub     # plumbing smoke test
uv run python -m bench.score --gt bench/cases --preds bench/preds/stub
```

Put real receipt photos + `gt.json` in `bench/cases/<id>/` (photos are
gitignored). See `bench/README.md` for candidate models and env vars.
