# Axisplit — Product & Technical Spec (v0.1 prototype)

## 1. What it is

A bill-splitting web app for groups (Splitwise/Tricount class) with one core
differentiator: **you photograph a paper receipt, OCR extracts the line items,
and every participant "claims" items by dropping colored tokens onto them.**
The split is derived from who owns which token on which item — no manual
transcription, no "who paid for what" arguments later.

v1 scope: restaurant/shop receipts. One tab = one event.

## 2. Domain model

```
User            username is the ID (text PK). No passwords in v1.
Tab             a group/event. created_by, name, invite_secret (capability token).
TabMember       (tab, username) + assigned color. Anyone in a tab may add anyone
                by username; usernames are claimed, not verified (see §6).
Receipt         an uploaded image inside a tab. uploaded_by, image file, status.
Item            one OCR-detected line: label, price (integer cents), currency,
                bbox (x,y,w,h normalized 0..1), ocr_confidence.
TokenAssignment (item, username, count) — "this person had count parts of
                this item". One row per (item, username); anyone can add
                anyone's token any number of times. This is the entire split state.
Confirmation    (receipt, username, confirmed_at) — user agrees with final
                token layout & prices of a receipt.
```

Removed receipts keep their data but are excluded from tab totals. Removal
requires explicit confirmation; Undo restores their assignments and agreement
state. Exact-photo duplicates reuse an existing active receipt. Existing copies
are flagged for review, never silently discarded. See `docs/ux.md`.


Settlement is **pure derived data**: for each item, each token holder owes
`price × count / total_count` (leftover cents go to the largest remainders,
ties by holder-sort order). One token each is an even split; 5 Marek + 1 Adam +
1 Nina on 7,00 € Nachos is 5,00 / 1,00 / 1,00 €. Debts between users are minimized optionally (greedy
matching); v1 shows the raw per-item ledger.

### Receipt status machine

```
UPLOADED  → OCR_RUNNING → AWAITING_ITEMS   (items need manual fix, UI editable)
                        → AWAITING_TOKENS  (items OK, no assignments yet)
AWAITING_TOKENS → SPLITTING (someone placing tokens)
SPLITTING       → PENDING_CONFIRM (a member clicked Confirm)
PENDING_CONFIRM → SETTLED (all items assigned and all token holders confirmed)
                          → anyone moving a token bumps it back to SPLITTING
```

## 3. Golden path (from the product description)

1. **Adam** creates tab "Brno dinner". Tab gets `invite_secret`; link
   `https://host/t/<tab_id>?s=<secret>`.
2. Adam posts link to group chat. **Nina** opens it, claims username "Nina".
   She is now a TabMember with an auto-assigned color.
3. Adam uploads a photo of the check. OCR runs → items with bboxes appear
   over the image: `GrillFor2 20€`, `Caesar Salad 10€`, `Nachos 5€`,
   `Beer x2 2€`, `Cola Zero 2€`.
4. Adam taps his own token in the member bar, taps the GrillFor2 box, taps
   Marek's token, taps GrillFor2 again → two tokens on that box.
5. Adam doesn't remember the rest → **leaves the room open state on the
   server**; nobody loses anything, everyone works from the share link.
6. Nina joins the receipt "room" (link again). She sees current tokens in
   real time. She taps her name to *claim herself as Nina* on this device,
   then places: Marek+Adam+Nina on Nachos, Nina on Beer, Nina on Salad,
   Marek on Cola, Adam on Beer.
7. Nina presses **Confirm** and pings the chat. Adam & Marek open, look,
   press Confirm. Receipt is SETTLED and feeds the tab ledger.

## 4. Realtime collaboration

- One **WebSocket room per receipt**: `/ws/receipts/<id>`.
- Clients send semantic ops: `place {item, user}`, `unplace {item, user}`,
  `confirm {user}`, presence pings. Server validates (sender must be a tab
  member) and broadcasts the resulting state event to all peers.
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
items (label, line-total price, box). A decision model (vllm-d3-nano, 2.4B,
Apache-2.0) served by llama.cpp's `/v1/systemone` picks which amount line is the
total, from the OCR text alone. Automatic on upload, retryable on empty receipts.
Benchmarked on 38 privately labelled receipts (rules tuned on 19, frozen, the
other 19 scored once). On the unseen 19, with a focus area: 219 of 244 prices
right and 1 extra item; totals right on 17 of 19 (rules alone 11; Qwen3-VL-4B
reading the photo 18, at ~8 s each). About a second per upload: two OCR passes on
an 8-core CPU, then 0.1 s on the GPU for the total. All results:
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
number (geometry only: shares and confirmations are unchanged). A token
can always be re-assigned, so OCR recall matters more than pixel-perfect IoU.

Coordinate-space guarantee: `Item.bbox` is normalized 0..1 against the **stored,
EXIF-transposed** file. Uploads run `normalize_pixels()` (Pillow
`exif_transpose` baked into the file), so raw pixels == rendered pixels ==
whatever space OCR models see. Backends MUST return boxes in that space.

## 6. Auth & threat model (v1)

- No accounts. A claimed username cookie per device + possession of the tab
  invite secret = capability to read/write the tab.
- The **shareable link is the secret** — leak = anyone can join the tab and
  impersonate a username. Acceptable for a friend-group prototype.
- Guardrails: invite secret never logged/rendered in URLs in listings,
  rotate-on-demand later. Verified accounts, password auth, and per-user
  ledgers are post-v1.

## 7. Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2 (async), Postgres in prod /
  SQLite in dev (`DATABASE_URL`). Jinja2 SSR.
- **Frontend:** htmx + Alpine.js (CDN), one small hand-written `receipt.js`
  for the token board + WebSocket. Mobile-first CSS. No React, no bundler
  in v1.
- **Interaction:** tap a member token, then **tap the photo** where the item is
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
  routers/          tabs, receipts, ws
  templates/ static/
```

## 9. Explicit non-goals (v1)

Payments/settlement outside the app, multi-currency conversion, expenses
without receipts, accounts, notifications, non-receipt documents, offline/PWA,
translations of OCR text.
