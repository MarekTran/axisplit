# Reader API, v1

axisplit can read receipts with a reader running as its own service, in any language and on any
hardware:

```bash
AXISPLIT_OCR_BACKEND=http AXISPLIT_READER_URL=http://127.0.0.1:9000 uv run uvicorn axisplit.main:app
```

A reader owns its tuning: it returns the receipt's items, printed total and currency, and the app
relies on nothing else. Before relying on one, score it on the labelled receipts with
`python -m bench.reader` (see its docstring). [`scripts/reader_service.py`](../scripts/reader_service.py)
serves the built-in reader this way and is a starting point for your own.

| Setting | Default | |
|---|---|---|
| `AXISPLIT_READER_URL` | (required) | the reader's base URL |
| `AXISPLIT_READER_KEY` | none | sent as `Authorization: Bearer <key>` |
| `AXISPLIT_READER_TIMEOUT` | `60` | seconds to wait for a reply (connecting: 5 s) |

`AXISPLIT_OCR_URL` is a different setting: the `vision` reader's chat-completions endpoint.

## Coordinates

Every coordinate is a fraction 0–1 of the photo the reader is sent, which is already
EXIF-normalized and turned as the user turned it. That holds even when a reader crops or resizes
internally.

- **A box** is `[x, y, w, h]`: its top-left corner, width and height. Both the width and the height are above 0.
- **A quad** is four corners `[[x, y], …]`, in the order top-left, top-right, bottom-right, bottom-left.

## `POST /v1/read`

The request is `multipart/form-data` with these fields:

| Field | |
|---|---|
| `image` | the photo (JPEG, PNG or WebP) |
| `area` | optional: the items area the user marked, as a JSON quad. Return only the items inside it. The total, currency and extent are the whole photo's. |

The reply is `200` with a JSON body:

```json
{
  "items": [
    {"label": "Beer 0,5l", "price_cents": 450, "box": [0.1, 0.205, 0.79, 0.015]},
    {"label": "Nachos", "price_cents": 890, "quad": [[0.1, 0.23], [0.89, 0.23], [0.89, 0.245], [0.1, 0.245]]}
  ],
  "total_cents": 1240,
  "currency": "EUR",
  "extent": [0.1, 0.205, 0.79, 0.19]
}
```

| Field | |
|---|---|
| `items` | required, may be empty |
| `items[].label` | 1–120 characters after trimming |
| `items[].price_cents` | the line total in cents, an integer ≥ 0 |
| `items[].box`, `items[].quad` | at least one. With a quad, the box is taken from the quad's bounds. |
| `total_cents` | the printed total as read from the photo, an integer ≥ 0, or `null`. Never the items' sum: the app checks the items against it. |
| `currency` | an ISO 4217 code such as `"EUR"`, or `null` |
| `extent` | a box: where the receipt is, used to frame it. Or `null`. |

Unknown fields are ignored.

**Partial results:** when only the total, currency or extent can't be read, return them as `null`
together with the items.

**Errors:** fail the request (any non-2xx status) only when the items can't be read. The app then
shows the receipt as failed and lets the user retry. A reply that breaks the rules above counts as
a failure too, and nothing from it is saved.

## `POST /v1/locate`

The request is `multipart/form-data` with an `image` field: a small copy of a photo the user is
still marking. The reply is `{"extent": [x, y, w, h]}`, or `{"extent": null}` when unknown. The app
uses it only to zoom that step onto the receipt.

## Try it

```bash
curl -s -F image=@receipt.jpg -F 'area=[[0.05,0.2],[0.95,0.2],[0.95,0.6],[0.05,0.6]]' \
  http://127.0.0.1:9000/v1/read
```
