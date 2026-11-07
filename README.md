# axisplit

Splitwise/Tricount for group receipts with receipt-OCR at the core: photograph a
check, OCR detects line items as bounding boxes, and each person "claims" items
by dropping their colored token onto them. The split is derived from tokens;
everyone confirms over a shareable link.

Read [`docs/spec.md`](docs/spec.md) for the domain model, flows, realtime
design, OCR plan, and stack decisions. [`docs/ux.md`](docs/ux.md) describes the
interaction design, duplicate recovery and accessibility decisions.

## Status

SSR FastAPI + Jinja app with automatic local OCR on upload, all on the CPU:
PaddleOCR (PP-OCRv6) reads the text lines, rules turn them into item names,
line-total prices and boxes, and a small decision model (vllm-d3-lite in
llama.cpp) picks which line is the receipt's total. An upload takes about 2.3 s
on an 8-core CPU, 0.8 s of it reading the photo ([Self-hosting](#self-hosting)). The
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

In a **second terminal**, start the decision model for totals:

```bash
nix run .#decide-server                                  # on the CPU; the first run fetches it (1.1 GB)
AXISPLIT_DECIDE_DEVICE=Vulkan1 nix run .#decide-server   # or on a GPU (llama-server --list-devices)
```

Open http://localhost:8000 — upload a receipt, mark the item lines, and
detection runs automatically. The first upload downloads the OCR models (31 MB).
Names, line-total prices and boxes are saved together; then place tokens and
confirm. Existing receipts with no items have a **Detect items automatically**
button; no re-upload required. Failed OCR is shown explicitly and can be
retried, never silently treated as successful. If the decision model isn't
running, the total comes from the rules alone (right on 11 of 19 unseen receipts,
against 17 of 19 with vllm-d3-lite) and the app logs a warning.

`AXISPLIT_DECIDE_URL` overrides the default `http://127.0.0.1:8082/v1`; any server
with the System One API (`/v1/systemone`) works, and `none` takes the rules alone.

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

A reader of your own can run as a separate service: `AXISPLIT_OCR_BACKEND=http` with
`AXISPLIT_READER_URL`. The API is in [`docs/reader-api.md`](docs/reader-api.md), and
`scripts/reader_service.py` serves the built-in reader that way as a template. Score a
reader on the labelled receipts with `python -m bench.reader` before relying on it.

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

## Self-hosting

The app reads receipts on the CPU, and a small decision model, also on the CPU, picks
each receipt's total. Without it, totals come from rules: right on 11 of 19 unseen
receipts, against 17 with it. Where the totals come from is the one choice:

| Totals from | Nix | Docker |
|---|---|---|
| the model, next to the app | `nix run github:MarekTran/axisplit` | `docker compose up -d` |
| a server of your own (a GPU) | `AXISPLIT_DECIDE_URL=http://gpu-box:8082/v1 nix run …` | `AXISPLIT_DECIDE_URL=… docker compose up -d app` |
| the rules alone | `AXISPLIT_DECIDE_URL=none nix run …` | `AXISPLIT_DECIDE_URL=none docker compose up -d app` |

Per upload, from the photo to the review page, on 46 labelled receipts (Ryzen 7 7800X3D):

| CPUs | Totals from | Median | Slowest | Slowest of two at once |
|---|---|---|---|---|
| 8 cores | the model | 2.3 s | 5.6 s | 8.1 s |
| 8 cores | the rules alone | 0.8 s | 1.3 s | 2.0 s |
| 4 of them | the model | 4.6 s | 9.0 s | 12.5 s |
| 4 of them | the rules alone | 1.7 s | 3.1 s | 4.4 s |

RAM: the app takes up to 0.8 GB and the model 1.6 GB. The model loads in under 2 s.

Nix builds the app from `uv.lock` with [uv2nix](https://github.com/pyproject-nix/uv2nix),
OCR models included. The decision model is vllm-d3-lite in llama.cpp, from the binary
cache; its weights (1.1 GB) are pinned by hash. The Docker images are built by Nix
too and hold only runtime closures: `axisplit` the app's, `axisplit-decide` the model's.

| Variable | Default | |
|---|---|---|
| `AXISPLIT_DATA` | `~/.local/share/axisplit`, `/data` in Docker | database and photos |
| `AXISPLIT_HOST`, `AXISPLIT_PORT` | `127.0.0.1`, `8000` | where the app listens |
| `DATABASE_URL` | SQLite in `AXISPLIT_DATA` | or `postgresql+asyncpg://…` |
| `AXISPLIT_DECIDE_URL` | the model next to the app | any server with `/v1/systemone`, or `none` |
| `AXISPLIT_DECIDE_MODEL`, `AXISPLIT_DECIDE_KEY` | | model name and API key, for vLLM |
| `AXISPLIT_DECIDE_TIMEOUT` | `15` | seconds; past it, an upload takes the rules' total |
| `AXISPLIT_DECIDE_THREADS` | one per CPU it may use | the model's threads; set it under a container `--cpus` limit |
| `AXISPLIT_DECIDE_PORT`, `AXISPLIT_DECIDE_DEVICE` | `8082`, `none` | the model's port, and a GPU for it (Nix) |

Docker Compose reads these from the shell or a `.env` file. The model listens on
loopback only, or on Compose's own network.

A server of your own needs `/v1/systemone`: llama.cpp has it (b11960 is tested), and so
does a vLLM [nightly](https://recipes.vllm.ai/vllm-sr/d3-nano). vllm-d3-nano gets 2 more
totals right than lite on the 38 labelled receipts, and is quick on a GPU:

```bash
AXISPLIT_DECIDE_DEVICE=Vulkan0 AXISPLIT_DECIDE_HOST=0.0.0.0 \
  nix run github:MarekTran/axisplit#decide-server                             # lite
llama-server -hf ggml-org/vllm-d3-nano-GGUF:Q8_0 --no-mmproj --ctx-size 8192 \
  -b 8192 -ub 8192 --parallel 1 --cache-ram 0 --host 0.0.0.0 --port 8082      # any llama.cpp build
vllm serve vllm-sr/d3-nano --api-key "$KEY"                                    # then also set
#   AXISPLIT_DECIDE_MODEL=vllm-sr/d3-nano AXISPLIT_DECIDE_KEY="$KEY"
```

On NixOS, with the flake as an input:

```nix
imports = [ axisplit.nixosModules.default ];
services.axisplit = {
  enable = true;   # http://127.0.0.1:8000, data in /var/lib/axisplit, the model on the CPU
  # host = "0.0.0.0"; openFirewall = true;
  # Totals from a server of your own instead, its key in a file outside the Nix store
  # (decide.enable = false alone: the rules):
  # decide.enable = false;
  # environment.AXISPLIT_DECIDE_URL = "http://gpu-box:8082/v1";
  # environmentFile = "/run/secrets/axisplit";
};
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
