# axisplit

Split a restaurant bill from a photo of the receipt. axisplit reads the items and prices off the
photo, you add who was there, and each person's colored token goes on what they had. The shares
follow from the tokens, and the review shows whether the items add up to the printed total.

There are no accounts: a receipt's link is its key. Anyone with the link can add people, assign
items and tick people off, live on every phone that has it open.

It runs on your own machine, on the CPU. No cloud service, no GPU needed.

## Run it

**Docker.** CI publishes the images from main, for x86_64:

```bash
curl -fsSLO https://raw.githubusercontent.com/MarekTran/axisplit/main/compose.yaml
docker compose up -d                  # http://localhost:8000
```

To build the images from source instead, clone the repository and run `docker compose up -d --build`.

**Nix**, with flakes enabled:

```bash
nix run github:MarekTran/axisplit     # http://127.0.0.1:8000
```

**NixOS**, with this flake as an input:

```nix
imports = [ axisplit.nixosModules.default ];
services.axisplit.enable = true;      # http://127.0.0.1:8000
# services.axisplit = { host = "0.0.0.0"; openFirewall = true; };
```

- **Requirements:** about 2.5 GB of RAM: 0.8 GB for the app, and 1.6 GB for the model that picks each receipt's total. Tested on x86_64 Linux.
- **Before exposing it:** there is no login, so anyone who can reach it can upload receipts. Put it behind a reverse proxy with TLS, and on the internet, behind authentication too.
  - **Reach:** `nix run` and NixOS listen on 127.0.0.1 only. Docker Compose publishes port 8000 on every address of the host.
- **Data:** an SQLite database and the photos.
  - **Where:** in `~/.local/share/axisplit` with `nix run`, in `/var/lib/axisplit` on NixOS, and in the `data` volume with Docker.
  - **Backups:** back up that location.
- **Updating:** `docker compose pull && docker compose up -d`, or `nix run --refresh github:MarekTran/axisplit`.

## Where the totals come from

OCR (PP-OCRv6) reads the text, and rules turn it into items and prices. A small language model,
vllm-d3-lite (1.1 GB, in llama.cpp), picks which line is the total. It runs on the CPU, next to
the app. You can point the app at a server of your own instead, such as one on a GPU, or leave the
model out and let the rules pick:

| Totals from | Nix | Docker |
|---|---|---|
| the model, next to the app | `nix run github:MarekTran/axisplit` | `docker compose up -d` |
| a server of your own | `AXISPLIT_DECIDE_URL=http://gpu-box:8082/v1 nix run …` | `AXISPLIT_DECIDE_URL=… docker compose up -d app` |
| the rules alone | `AXISPLIT_DECIDE_URL=none nix run …` | `AXISPLIT_DECIDE_URL=none docker compose up -d app` |

On NixOS, `services.axisplit.decide.enable = false` gives the rules alone. To use a server of
your own, also set `environment.AXISPLIT_DECIDE_URL`.

A server of your own needs llama.cpp's `/v1/systemone` API. llama.cpp has it (b11960 is
tested), and so does a vLLM [nightly](https://recipes.vllm.ai/vllm-sr/d3-nano). On a GPU,
vllm-d3-nano is a little more accurate than lite (see [below](#how-well-it-works)):

```bash
llama-server -hf ggml-org/vllm-d3-nano-GGUF:Q8_0 --no-mmproj --ctx-size 8192 \
  -b 8192 -ub 8192 --parallel 1 --cache-ram 0 --host 0.0.0.0 --port 8082
vllm serve vllm-sr/d3-nano --api-key "$KEY"   # also set AXISPLIT_DECIDE_MODEL and AXISPLIT_DECIDE_KEY
```

## Settings

Environment variables. Docker Compose reads them from the shell or from a `.env` file next to
`compose.yaml`.

| Variable | Default | |
|---|---|---|
| `AXISPLIT_HOST`, `AXISPLIT_PORT` | `127.0.0.1`, `8000` | where the app listens |
| `AXISPLIT_DATA` | see [Run it](#run-it) | database and photos |
| `DATABASE_URL` | SQLite in `AXISPLIT_DATA` | or `postgresql+asyncpg://…` |
| `AXISPLIT_DECIDE_URL` | the model next to the app | a server with `/v1/systemone`, or `none` |
| `AXISPLIT_DECIDE_MODEL`, `AXISPLIT_DECIDE_KEY` | | model name and API key, for vLLM |
| `AXISPLIT_DECIDE_TIMEOUT` | `15` | seconds; past it, an upload takes the rules' total |
| `AXISPLIT_DECIDE_THREADS` | one per CPU it may use | the model's threads; set it under a container `--cpus` limit |
| `AXISPLIT_DECIDE_PORT` | `8082` | the bundled model's port |
| `AXISPLIT_DECIDE_DEVICE` | `none` | a GPU for the bundled model (`nix run`), such as `Vulkan0` |

## How well it works

These are measured on 38 of the maintainer's own receipts. The rules were tuned on 19 of them,
then scored once on the other 19. The machine is an 8-core Ryzen 7 7800X3D. The method and every
result are in [`docs/benchmarks.md`](docs/benchmarks.md).

On the 19 held-out receipts, with the item lines marked as on upload, it found 220 of 244 items,
219 of them with the exact price. That's from scoring them once, before looking at its mistakes;
today's code gets 224 exact prices on them. Totals right:

| Totals from | Held-out receipts | Receipts used for tuning |
|---|---|---|
| vllm-d3-lite (bundled) | 17 of 19 | 15 of 19 |
| vllm-d3-nano | 17 of 19 | 17 of 19 |
| the rules alone | 11 of 19 | 17 of 19 |

- **Vision models:** reading the photo with one (Qwen3-VL) got 18 of 19 totals right, but far fewer prices, at 7–10 s per receipt on a GPU.
- **A wrong total isn't silent:** the review then shows that the items don't add up.

Time per upload, from the photo to the review page:

| CPUs | Totals from | Median | Slowest | Slowest of two at once |
|---|---|---|---|---|
| 8 cores | the model | 2.3 s | 5.6 s | 8.1 s |
| 8 cores | the rules alone | 0.8 s | 1.3 s | 2.0 s |
| 4 of them | the model | 4.6 s | 9.0 s | 12.5 s |
| 4 of them | the rules alone | 1.7 s | 3.1 s | 4.4 s |

## Development

Everything runs in the flake's dev shell:

```bash
nix develop                           # or: direnv allow
uv sync --all-extras
uv run uvicorn axisplit.main:app --reload --host 0.0.0.0 --port 8000
nix run .#decide-server               # the total-picking model, in a second terminal
```

To run the model on a GPU instead, set `AXISPLIT_DECIDE_DEVICE` (for example, `Vulkan0`;
`llama-server --list-devices` lists them).

Tests (CI runs the first two on every pull request, along with the Nix build):

```bash
uv run pytest -q
nix shell --inputs-from . nixpkgs#nodejs -c node --test tests/hit-test.test.cjs
nix shell --inputs-from . nixpkgs#geckodriver -c \
  uv run --with selenium python scripts/ux_browser.py      # two Firefox sessions on one link
AXISPLIT_TEST_DATABASE_URL='postgresql+asyncpg://…/axisplit_test' \
  uv run pytest -q tests/test_ux.py                         # on Postgres; that database is emptied
```

To run against Postgres in development, start it with `nix build .#postgres-init && ./result/bin/pg-dev`,
then set `DATABASE_URL`.

**Other ways to read receipts,** chosen with `AXISPLIT_OCR_BACKEND`:
- **`vision`:** a vision model, served by the dev shell's `axisplit-ocr`.
- **`http`:** a reader of your own, running as a service. Its API is in [`docs/reader-api.md`](docs/reader-api.md).

To score a reader on the labelled receipts, see [`bench/README.md`](bench/README.md).

**Design:**
- [`docs/spec.md`](docs/spec.md): the domain model, flows and OCR pipeline.
- [`docs/ux.md`](docs/ux.md): the interaction design.

## License

MIT, see [`LICENSE`](LICENSE). The bundled models keep their own licenses: vllm-d3-lite and the
PP-OCRv6 models are Apache-2.0.
