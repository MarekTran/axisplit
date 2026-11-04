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
TokenAssignment (item, username) — "this person consumed/owns this item".
                Unique per (item, username). This is the entire split state.
Confirmation    (receipt, username, confirmed_at) — user agrees with final
                token layout & prices of a receipt.
```

Settlement is **pure derived data**: for each item, each token holder owes
`price / n_holders` (remainder cents distributed deterministically by
holder-sort order). Debts between users are minimized optionally (greedy
matching); v1 shows the raw per-item ledger.

### Receipt status machine

```
UPLOADED  → OCR_RUNNING → AWAITING_ITEMS   (items need manual fix, UI editable)
                        → AWAITING_TOKENS  (items OK, no assignments yet)
AWAITING_TOKENS → SPLITTING (someone placing tokens)
SPLITTING       → PENDING_CONFIRM (a member clicked Confirm)
PENDING_CONFIRM → SETTLED (all *participants listed on the receipt* confirmed)
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
- Ops are add/remove on a set → **commutative & idempotent**. No CRDT needed;
  server is authoritative, client applies optimistic update then reconciles
  with broadcast (no-op if already applied). Ordering conflicts (place vs
  remove of same token simultaneously) resolve last-write-wins at the server.
- Presence: `presence {user, viewing}` heartbeat every 15 s; UI shows avatars
  of who is in the room.
- HTTP fallback (htmx POST) uses the same op handlers; WS is progressive
  enhancement for live peer updates.

## 5. OCR pipeline

Goal: from one crumpled receipt photo → `[{label, price_cents, currency, bbox}]`.

**Decision pending benchmark** (see `/bench`):
- Primary candidates: `dots.ocr` (native bbox+category+text JSON, MIT),
  `Qwen3-VL-8B` (grounding-capable general VLM, easy via LiteLLM/llama.cpp),
  `DeepSeek-OCR-2` (Apache-2.0, strong structure reading),
  `Nanonets-OCR2/3` (receipt-tuned).
- Fallback hybrid: `PP-OCRv6` detection for precise geometry + VLM for
  item/price semantics.
- Classical-only (Tesseract) is ruled out for crumpled thermal paper.

Pipeline contract in code: `axisplit.ocr.extract_items(path) -> list[ExtractedItem]`.
Prototype ships with a `StubBackend` (empty/placeholder items, manual entry UI)
so the app is fully usable before OCR is decided.

UI tolerance: boxes may be loose — **users tap anywhere on the photo where they
see an item**, and the tap resolves against OCR geometry client-side: first a
generous containment test (bbox expanded by 4% of image size), else the nearest
box center within 25% of image size; no match → "add this item" hint. A token
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

- **Backend:** Python 3.13, FastAPI, SQLAlchemy 2 (async), Postgres in prod /
  SQLite in dev (`DATABASE_URL`). Jinja2 SSR.
- **Frontend:** htmx + Alpine.js (CDN), one small hand-written `receipt.js`
  for the token board + WebSocket. Mobile-first CSS. No React, no bundler
  in v1.
- **Interaction:** tap a member token, then **tap the photo** where the item is
  printed; client-side geometry snaps the token to the containing/nearest OCR
  box (spec §5). Tap a pip to remove. True DnD deliberately avoided on touch —
  tap-tap also works one-handed on a phone at a restaurant table.
- **Inference:** models served behind LiteLLM proxy (OpenAI-compatible) or
  run locally from the bench harness.
- **Dev env:** Nix flake (python, uv, postgres, git) + direnv.

## 8. Repo layout

```
docs/spec.md        this file
bench/              OCR benchmark harness + ground-truth format
src/axisplit/       FastAPI app (SSR)
  ocr.py            extraction contract + stub backend
  models.py db.py   SQLAlchemy domain
  routers/          tabs, receipts, ws
  templates/ static/
```

## 9. Explicit non-goals (v1)

Payments/settlement outside the app, multi-currency conversion, expenses
without receipts, accounts, notifications, non-receipt documents, offline/PWA,
translations of OCR text.
