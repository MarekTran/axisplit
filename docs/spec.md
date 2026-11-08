# Axisplit — Product & Technical Spec (v0.1 prototype)

## 1. What it is

A receipt-splitting calculator with one core differentiator: **you photograph a
paper receipt, OCR extracts the line items, and each person's colored token is
tapped onto what they had.** The split is derived from who owns which token on
which item — no manual transcription, no "who paid for what" arguments later.

v1 scope: restaurant/shop receipts. One receipt = one split = one link. Usually
one person (the one with the bill) does it all and ticks people off as they
check their share; anyone they share the link with can help.

## 2. Domain model

```
Receipt         an uploaded image, its status and currency. Its random id is the
                link and the only key to it (see §6).
Person          (receipt, username) + color + agreed_cents. Added by name on the
                receipt by anyone with the link; names are unique on it.
                agreed_cents: the share they were ticked off at (null: not ticked).
Item            one OCR-detected line: label, price (integer cents), currency,
                bbox (x,y,w,h normalized 0..1), ocr_confidence.
TokenAssignment (item, username, count) — "this person had count parts of
                this item". One row per (item, username); anyone can add
                anyone's token any number of times. This is the entire split state.
```

A person is ticked off while their share still equals `agreed_cents`: a change
that moves their share unticks only them, and undoing it ticks them again. Ticks
are optional; nothing waits for them. Removing a person drops their tokens.


Settlement is **pure derived data**: for each item, each token holder owes
`price × count / total_count` (leftover cents go to the largest remainders,
ties by holder-sort order). One token each is an even split; 5 Marek + 1 Adam +
1 Nina on 7,00 € Nachos is 5,00 / 1,00 / 1,00 €. Debts between users are minimized optionally (greedy
matching); v1 shows the raw per-item ledger.

### Receipt status machine

```
UPLOADED  → OCR_RUNNING → AWAITING_ITEMS   (items need manual fix, UI editable)
                        → AWAITING_TOKENS  (items OK, no assignments yet)
AWAITING_TOKENS → SPLITTING       (tokens placed, some items still unassigned)
SPLITTING       → PENDING_CONFIRM ("All assigned": every item has a token)
PENDING_CONFIRM → SETTLED         ("Everyone agreed": everyone with a share is
                                   ticked off at that share)
Derived after every change, so it moves back as readily as forward.
```

## 3. Golden path (from the product description)

1. **Adam** has the bill. He uploads a photo of the check. OCR runs → items
   with bboxes appear over the image: `GrillFor2 20€`, `Caesar Salad 10€`,
   `Nachos 5€`, `Beer x2 2€`, `Cola Zero 2€`.
2. He adds Adam, Marek and Nina by name in the people bar.
3. He picks Adam, taps the GrillFor2 box, picks Marek, taps GrillFor2 again →
   two tokens on that box.
4. He doesn't remember the rest → **Share link** to the group chat. The
   receipt's state lives on the server; nobody loses anything.
5. **Nina** opens the link (no sign-in) and sees the tokens live. She picks
   names and places: Marek+Adam+Nina on Nachos, Nina on Beer, Nina on Salad,
   Marek on Cola, Adam on Beer.
6. Adam goes round the table: each person's row lists what they had and their
   share. He ticks them off as they agree. Receipt is SETTLED.

## 4. Realtime collaboration

- One **WebSocket room per receipt**: `/ws/receipts/<id>`.
- Clients send semantic ops: `place {item, user}`, `unplace {item, user}`,
  `confirm {user}` / `unconfirm {user}` (the tick), presence pings. Ops name
  any person on the receipt; the server checks they are on it and broadcasts
  the resulting state event to all peers. Adding or removing people is HTTP and
  broadcasts `people`; peers re-read the review, which carries the people bar.
- `place` adds one more of that person's tokens, `unplace` takes one off; the
  server applies each as a single atomic UPDATE/DELETE, so the per-person
  counters are **commutative** and concurrent taps never lose a count. No
  CRDT needed; server is authoritative, client shows the new count
  immediately, queues its ops in order, and reconciles with the server's
  review once they settle. `unplace` at zero is a no-op.
- Presence: `presence {user, viewing}` heartbeat every 15 s; UI shows avatars
  of who is in the room.
- HTTP fallback (htmx POST) uses the same op handlers; WS is progressive
  enhancement for live peer updates.

## 5. OCR pipeline

Goal: from one crumpled receipt photo → `[{label, price_cents, currency, bbox}]`.

**Readers.** The app reads receipts through one reader (`ocr.Backend`), chosen with
`AXISPLIT_OCR_BACKEND`. Its contract is two calls:
- `read(photo, area)` returns the items inside the marked area, with the printed total,
  currency and extent of the whole photo;
- `locate(photo)` returns where the receipt is, for the focus step.

A reader owns its tuning, because its parts are tuned together; the app relies only on the
contract. Only the items are required: a reader returns them even when the other parts
fail. The total is read on its own, so it can check the items. Readers:
- `paddle`, the default;
- `vision`;
- `http`: a reader running as its own service. Its API is in
  [`reader-api.md`](reader-api.md), and every reply is validated before it's saved.
- `stub`, for tests.

`python -m bench.reader` scores any reader through the same `read()` call.

**Current pipeline (`PaddleBackend`, the default):** PP-OCRv6 small through
RapidOCR on the CPU reads the text lines and their quads. `receipt_rules` joins
them into printed rows along the text's slope, finds the price column and returns
items (label, line-total price, box). A decision model (vllm-d3-lite, 1.0B,
Apache-2.0) served by llama.cpp's `/v1/systemone`, also on the CPU, picks which
amount line is the total, from the OCR text alone. Automatic on upload, retryable on empty receipts.
Benchmarked on 38 privately labelled receipts (rules tuned on 19, frozen, the
other 19 scored once). On the unseen 19, with a focus area: 219 of 244 prices
right and 1 extra item; totals right on 17 of 19 (rules alone 11; Qwen3-VL-4B
reading the photo 18, at ~8 s each). Per upload on an 8-core CPU, 2.3 s at the
median: 0.8 s for two OCR passes, the rest for the total (0.1 s with the model on a
GPU); 4.6 s on 4 CPUs. All results:
[`docs/benchmarks.md`](benchmarks.md).

**Vision backend** (`AXISPLIT_OCR_BACKEND=vision`): Qwen3-VL-4B via a local
llama.cpp server (Nix-packaged Vulkan backend), JSON-schema-constrained
item/price/box extraction.

**Candidates for further benchmarking** (see `/bench`):
- Primary candidates: `dots.ocr` (native bbox+category+text JSON, MIT),
  `Qwen3-VL-8B` (grounding-capable general VLM, easy via LiteLLM/llama.cpp),
  `DeepSeek-OCR-2` (Apache-2.0, strong structure reading),
  `Nanonets-OCR2/3` (receipt-tuned).
- Fallback hybrid: `PP-OCRv6` detection for precise geometry + VLM for
  item/price semantics.
- Classical-only (Tesseract) is ruled out for crumpled thermal paper.

Pipeline contract in code: `axisplit.ocr.extract_items(path, focus=None) -> list[ExtractedItem]`
and `axisplit.ocr.extract_total(path) -> int | None`.

**Focus area.** After choosing a photo, the uploader drags over the item lines and
their prices (or picks "Use whole photo"). The area is stored on the receipt and
trusted as drawn: OCR receives only that crop (the vision backend upscales it so its
longer side is 1600 px), so the grand total and other numbers can't be read as items, and returned boxes are
mapped back onto the whole photo. On the demo receipt with the local Qwen VLM this
turned 2 wrong prices and drifting boxes (whole photo) into 7 correct prices with one
box per line; one receipt, not a benchmark.

**Total cross-check.** A second, independent OCR call reads the printed grand total
from the whole photo. The review shows whether the items add up to it, or how much
is missing or too much, so a missed line or misread price is visible. A failed
total read never blocks the items. With `PaddleBackend` the total pass is the
whole-photo OCR plus one decision-model question; if the decision server is down,
the rules' own total is used and a warning is logged.
The default is `PaddleBackend` (decision server at `http://127.0.0.1:8082/v1`);
`LocalVisionBackend` talks to `http://127.0.0.1:8081/v1`.
`StubBackend` is only enabled explicitly with `AXISPLIT_OCR_BACKEND=stub` for
UI tests. Inference errors are logged and shown as detection failures, never
silently replaced by fake boxes. Qwen boxes in 0..1000 convert to normalized
0..1 after validating coordinate order and bounds.

UI tolerance: boxes may be loose — **users tap anywhere on the photo where they
see an item**, and the tap resolves against OCR geometry client-side: actual
box containment first, then 12 rendered pixels of hit slop, then nearest box
at any distance. Overlapping candidates are ranked by distance to the box
edge, then its center; padded boxes never steal hits inside another actual
box. An empty receipt offers detection or manual recovery. A missing item is
added by dragging its line on the photo; a wrong box is redrawn from the item's
number (geometry only: shares and ticks are unchanged). A token
can always be re-assigned, so OCR recall matters more than pixel-perfect IoU.

Coordinate-space guarantee: `Item.bbox` is normalized 0..1 against the **stored,
EXIF-transposed** file. Uploads run `normalize_pixels()` (Pillow
`exif_transpose` baked into the file), so raw pixels == rendered pixels ==
whatever space OCR models see. Backends MUST return boxes in that space.

## 6. Auth & threat model (v1)

- No accounts, no names to claim. Possession of a receipt's link (its random
  id) = full rights on it: add and remove people, assign, tick, edit items.
- The **shareable link is the secret** — leak = anyone can change the split.
  Acceptable for a friend-group calculator.
- Guardrails: receipts are never listed to others; the home page lists only
  those opened on the device (a cookie of ids). Verified accounts and per-user
  ledgers are post-v1.

## 7. Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2 (async), Postgres in prod /
  SQLite in dev (`DATABASE_URL`). Jinja2 SSR.
- **Frontend:** htmx + Alpine.js (CDN), one small hand-written `receipt.js`
  for the token board + WebSocket. Mobile-first CSS. No React, no bundler
  in v1.
- **Interaction:** tap a person's token, then **tap the photo** where the item is
  printed; client-side geometry snaps the token to the containing/nearest OCR
  box (spec §5). Tapping again adds another token (a bigger part); tap a pip
  to take one off. The item list works the same way: a row adds the selected
  person, a person's chip takes one of their tokens off. True DnD deliberately avoided on touch —
  tap-tap also works one-handed on a phone at a restaurant table.
- **Inference:** models served behind LiteLLM proxy (OpenAI-compatible) or
  run locally from the bench harness.
- **Dev env:** Nix flake (python, uv, postgres, git) + direnv.

## 8. Repo layout

```
docs/spec.md        this file
docs/benchmarks.md  OCR benchmark results
bench/              OCR benchmark harness + ground-truth format
src/axisplit/       FastAPI app (SSR)
  ocr.py            local vision inference + validated grounding boxes
  models.py db.py   SQLAlchemy domain
  routers/          home, receipts, ws
  templates/ static/
```

## 9. Explicit non-goals (v1)

Payments/settlement outside the app, multi-currency conversion, expenses
without receipts, accounts, notifications, non-receipt documents, offline/PWA,
translations of OCR text.
