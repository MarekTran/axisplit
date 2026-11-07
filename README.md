# axisplit

A receipt-splitting calculator with receipt-OCR at the core: photograph a
check, OCR detects line items as bounding boxes, add who's splitting, and tap
each person's colored token onto what they had. The split is derived from the
tokens. No accounts: the receipt's link is the key, and anyone with it can add
people, assign items and tick people off once they've checked their share.

Read [`docs/spec.md`](docs/spec.md) for the domain model, flows, realtime
design, OCR plan, and stack decisions. [`docs/ux.md`](docs/ux.md) describes the
interaction design, duplicate recovery and accessibility decisions.

## Status

SSR FastAPI + Jinja app with automatic local OCR on upload, in about a second:
PaddleOCR (PP-OCRv6, CPU) reads the text lines, rules turn them into item names,
line-total prices and boxes, and a small decision model (vllm-d3-nano in
llama.cpp, Vulkan on Linux/AMD) picks which line is the receipt's total. The
review shows whether the items add up to that total. Predictions need checking:
the add-up check catches a missed line or misread price, not every mistake.

## Dev quickstart (NixOS or any Nix flake machine)

Everything runs inside the pinned flake dev shell — do **not** use your ambient
Python (the flake pins python312 + postgres; `uv sync` inside it builds the venv
from the lockfile):

```bash
direnv allow            # auto-enter shell, or manually: nix develop
uv sync --all-extras    # deps into .venv
uv run uvicorn axisplit.main:app --reload --host 0.0.0.0 --port 8000
```

In a **second dev-shell terminal**, start the decision model for totals:

```bash
nix develop
axisplit-decide                    # first launch downloads 2.6 GB into ~/.cache/axisplit-models
axisplit-decide --device Vulkan1   # pick a GPU (llama-server --list-devices); --device none for CPU only
```

Open http://localhost:8000 — upload a receipt, mark the item lines, and
detection runs automatically. The first upload downloads the OCR models (31 MB).
Names, line-total prices and boxes are saved together; then add people, place
tokens and, optionally, tick each person off. Existing receipts with no items have a **Detect items automatically**
button; no re-upload required. Failed OCR is shown explicitly and can be
retried, never silently treated as successful. If the decision model isn't
running, the total comes from the rules alone (right on 11 of 19 unseen receipts,
against 17 of 19 with vllm-d3-nano) and the app logs a warning.

`AXISPLIT_DECIDE_URL` overrides the default `http://127.0.0.1:8082/v1`; any server
with the System One API (`/v1/systemone`) works. `AXISPLIT_DECIDE_HF` picks another
decision GGUF for `axisplit-decide`.

The previous pipeline, a vision model that reads items, boxes and the total from
the photo (Qwen3-VL-4B, about 8 s per receipt on a GPU), is still available:

```bash
axisplit-ocr                                          # vision server on :8081, ~3 GB download
AXISPLIT_OCR_BACKEND=vision uv run uvicorn axisplit.main:app --reload
AXISPLIT_OCR_HF=ggml-org/GLM-OCR-GGUF axisplit-ocr    # GLM-OCR experiments
```

GLM's documented layout pipeline uses a separate detector; the vision backend's
prompt requests grounding boxes directly and is tested with Qwen, not GLM.
Mistral OCR is a hosted service, not a downloadable llama.cpp model. InternVL
and Gemma are alternatives to benchmark, not drop-in guarantees of accurate boxes.
`AXISPLIT_OCR_URL` overrides the vision server's default `http://127.0.0.1:8081/v1`.

Each receipt's **Share link** button shares its URL; whoever opens it can help
split, live. The home page lists the receipts opened on that device.

Dev DB is SQLite under `./data/` (`splits.db`; an `axisplit.db` from the earlier
tab version is left as it is, not converted; recreate a Postgres database from it). Postgres (still inside `nix develop`):

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

Results so far, and why the app uses PaddleOCR plus a decision model:
[`docs/benchmarks.md`](docs/benchmarks.md).
