# OCR benchmark — pick the receipt engine

Goal: measure candidate models on **real crumpled receipt photos** for
axisplit's need: `{label, price_cents, bbox}` per line item (see docs/spec.md §5).
Priority: **item recall & price accuracy > box IoU** (boxes are advisory;
users re-place tokens by hand).

## Layout

```
bench/
  run.py       # inference: <case-image> -> predictions.json
  score.py     # metrics: predictions vs ground truth
  metrics.py schema.py
  cases/
    <case-id>/
      photo.jpg|png|webp     # gitignored (privacy + repo size)
      gt.json                # ground truth, committed
```

## Ground truth format (`gt.json`)

```json
{
  "image": "photo.jpg",
  "currency": "EUR",
  "items": [
    {"label": "GrillFor2",  "price_cents": 2000, "bbox": [0.08, 0.21, 0.62, 0.27]},
    {"label": "Beer x2",    "price_cents": 400,  "bbox": [0.08, 0.55, 0.55, 0.60]}
  ]
}
```

`bbox` = normalized `[x1, y1, x2, y2]`, 0..1 of the original pixel size.
Prices are the **line total** (Beer x2 at 2€ -> 400).

Optional fields, used by the labelled `test_photos` cases:

- `quad`: the same item as 4 normalized points `[[x, y] TL, TR, BR, BL]` that follow the
  printed text's slope, so tilted and curled receipts don't overlap their neighbours.
  `bbox` stays the axis-aligned extent, for scoring against rectangle predictions.

- `image` may point outside the case dir (`../../../test_photos/<id>.jpg`);
  `run.py` uses it when the file exists.
- `total`: the printed grand total, `{label, price_cents, bbox}`, plus `note`
  when something about it is unusual (e.g. cut off by the photo edge).
- `discounts`: priced lines that reduce the bill (negative `price_cents`),
  kept out of `items` because the app's OCR contract excludes them.

`items` + `discounts` always add up to `total` (checked by `tests/test_bench_gt.py`).

### Labelled cases (`test_photos`, 8 receipts)

Each item box spans everything printed for that item: the name, a wrapped
name, a quantity/unit-price line and the line total. Category headings and
column headers are not items; add-ons with their own price (`* Cheese 1,50`)
and bottle deposits (`PET obal záloha`) are. Labels are the item name as printed,
without the quantity prefix or unit price.

Quads use the text slope read off each photo (name vs. price heights), per height
on the page, so a curl gets a different slope per line; each quad is then trimmed
to its line's ink. The photos are never transformed.

| case | receipt | items | total |
|---|---|---|---|
| 9DBn9iLb | Café Letka, Praha | 9 | 1 035,00 CZK |
| OnC558eX | supermarket, Bratislava (curled, 9 discounts, total at photo edge) | 33 | 88,05 EUR |
| RhRwCpua | slatinsky.sk, Bratislava | 7 | 61,80 EUR |
| ctUi2Puk | Cantina Caliente (hand-held, perspective) | 8 | 74,50 EUR |
| es4qgUmN | Little V | 5 | 64,00 EUR |
| sWJSf9a8 | Susan Bijl, Rotterdam | 3 | 74,70 EUR |
| u78-VYhI | restaurant, tilted | 5 | 153,90 EUR |
| vrVOOVJL | Restaurace Bullerbyn, Praha (tilted) | 3 | 610,00 CZK |

## Running

```bash
# 1. list models
uv run python bench/run.py --list

# 2. run one model over all cases (needs LiteLLM proxy or local model)
uv run python bench/run.py --model dots.ocr --out bench/preds/dots.ocr
AXISPLIT_LITELLM_KEY=... uv run python bench/run.py --model qwen3-vl-8b --out ...

# 3. score
uv run python bench/score.py --gt bench/cases --preds bench/preds/dots.ocr
```

## Candidate models (researched 2026-11)

| id              | what                                        | license     |
|-----------------|---------------------------------------------|-------------|
| dots.ocr        | layout-VLM, native bbox+category+text JSON   | MIT         |
| qwen3-vl-8b     | general VLM w/ OCR grounding, OpenAI-compat  | apache      |
| deepseek-ocr-2  | OCR-native VLM (visual causal flow)          | apache-2.0  |
| nanonets-ocr3   | receipt-finetuned                            | check       |
| ppocr-v6        | classical det+rec hybrid baseline            | apache      |
| unlimited-ocr-2 | newest OCR VLM (Baidu, Nov '26)              | MIT         |

Excluded: chandra-ocr-2 (OpenRail non-commercial).

## Metrics

- **item recall** — GT items matched to a prediction by best-IoU box
  (IoU ≥ 0.3) *and* fuzzy label (token-set ratio ≥ 0.6)
- **price exact** — matched items whose price_cents agree exactly
- **price within 1%** — tolerance for rounding/qty mistakes
- **mean IoU** of matched boxes
- **hallucination rate** — predicted items not matching any GT box
- **latency & cost** per receipt (manual note in results table)

## Comparing local vision models (`bench/vlm.py`)

Runs each model through the app's own OCR code (`axisplit.ocr`, same prompt and
llama-server flags as `axisplit-ocr`) and scores it against the quads above. Per
receipt, three calls: items from the whole photo, items from the focus area a user
would drag (the ground-truth items' extent plus 1%), and the printed total.

```bash
# one model server at a time, on a GPU that isn't driving the display
python -m bench.vlm run --models qwen3-vl-2b qwen3-vl-4b \
    --llama-server "$(grep -o '/nix/store/[^ ]*/bin/llama-server' "$(which axisplit-ocr)")" \
    --device Vulkan1 --max-ram-gb 8
python -m bench.vlm score            # markdown table; --json for raw rows
```

`--max-ram-gb` runs the server in a memory-capped systemd scope: a model that
outgrows it is killed alone instead of the desktop. Models download into
`~/.cache/axisplit-models`; predictions go to `bench/preds/vlm/` (gitignored).

Columns: **items_found** ground-truth items matched by name (quantity prefix and
accents ignored); **price_right** matched with the exact price; **box_on_line** matched
and the predicted box's centre lies inside the item's quad (a tap there hits the right
item); **mean_iou** predicted rectangle vs. quad; **extra_items** predictions that match
nothing; **sum_exact** receipts whose predicted prices add up to the items' total;
**total_right** the separate total read; **failed_calls** answers the app rejects
(box coordinate outside 0..1000, or cut off at 4096 tokens); **median_s** per items
call. Size is the model file plus its vision projector.

### Results (2026-11-05, llama.cpp build 11960, Vulkan, one RX 9070 XT, Q4 quants)

| model | size_gb | mode | items_found | price_right | box_on_line | mean_iou | extra_items | sum_exact | total_right | failed_calls | median_s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gemma-4-12b | 7.4 | whole | 72/73 | 57/73 | 43/73 | 0.39 | 0 | 5/8 | 8/8 | 0 | 5.2 |
| gemma-4-12b | 7.4 | focus | 70/73 | 58/73 | 62/73 | 0.55 | 3 | 7/8 | 8/8 | 0 | 5.6 |
| gemma-4-e2b | 3.4 | whole | 71/73 | 39/73 | 25/73 | 0.08 | 2 | 1/8 | 6/8 | 0 | 2.5 |
| gemma-4-e2b | 3.4 | focus | 39/73 | 29/73 | 14/73 | 0.07 | 3 | 4/8 | 6/8 | 1 | 3.0 |
| gemma-4-e4b | 5.2 | whole | 66/73 | 48/73 | 25/73 | 0.11 | 6 | 3/8 | 7/8 | 0 | 4.4 |
| gemma-4-e4b | 5.2 | focus | 70/73 | 57/73 | 24/73 | 0.06 | 1 | 4/8 | 7/8 | 0 | 3.5 |
| gemma-4-e4b-thinking | 5.2 | whole | 38/73 | 34/73 | 8/73 | 0.17 | 1 | 5/8 | 7/8 | 1 | 15.3 |
| gemma-4-e4b-thinking | 5.2 | focus | 40/73 | 39/73 | 6/73 | 0.06 | 0 | 6/8 | 7/8 | 1 | 16.3 |
| glm-ocr | 1.4 | whole | 2/73 | 0/73 | 0/73 | 0.0 | 3 | 0/8 | 4/8 | 3 | 1.5 |
| glm-ocr | 1.4 | focus | 12/73 | 8/73 | 1/73 | 0.09 | 2 | 1/8 | 4/8 | 1 | 1.6 |
| qwen3-vl-2b | 1.6 | whole | 66/73 | 32/73 | 28/73 | 0.24 | 1 | 0/8 | 6/8 | 1 | 2.2 |
| qwen3-vl-2b | 1.6 | focus | 40/73 | 23/73 | 34/73 | 0.28 | 0 | 3/8 | 6/8 | 1 | 2.2 |
| qwen3-vl-4b | 3.0 | whole | 71/73 | 49/73 | 27/73 | 0.16 | 1 | 5/8 | 8/8 | 0 | 3.3 |
| qwen3-vl-4b | 3.0 | focus | 39/73 | 34/73 | 38/73 | 0.25 | 0 | 4/8 | 8/8 | 1 | 3.4 |
| qwen3-vl-8b | 5.8 | whole | 72/73 | 66/73 | 30/73 | 0.29 | 0 | 4/8 | 8/8 | 0 | 4.5 |
| qwen3-vl-8b | 5.8 | focus | 40/73 | 38/73 | 37/73 | 0.49 | 0 | 5/8 | 8/8 | 1 | 5.1 |

In focus mode, Qwen3-VL 2B/4B/8B and Gemma 4 E2B each write one coordinate a few
units over 1000 for the supermarket receipt's last line, at the crop's edge, and the
app rejects the whole answer: 33 of the 73 items. The same 7 other receipts (40 items):

| model | size GB | mode | price right | box on line | sum exact |
|---|---|---|---|---|---|
| qwen3-vl-2b | 1.6 | whole | 11/40 | 16/40 | 0/7 |
| qwen3-vl-2b | 1.6 | focus | 23/40 | 34/40 | 3/7 |
| gemma-4-e2b | 3.4 | whole | 21/40 | 16/40 | 1/7 |
| gemma-4-e2b | 3.4 | focus | 29/40 | 14/40 | 4/7 |
| qwen3-vl-4b | 3.0 | whole | 37/40 | 21/40 | 5/7 |
| qwen3-vl-4b | 3.0 | focus | 34/40 | 38/40 | 4/7 |
| gemma-4-e4b | 5.2 | whole | 30/40 | 17/40 | 3/7 |
| gemma-4-e4b | 5.2 | focus | 33/40 | 9/40 | 4/7 |
| qwen3-vl-8b | 5.8 | whole | 37/40 | 19/40 | 4/7 |
| qwen3-vl-8b | 5.8 | focus | 38/40 | 37/40 | 5/7 |
| gemma-4-12b | 7.4 | whole | 26/40 | 38/40 | 5/7 |
| gemma-4-12b | 7.4 | focus | 28/40 | 34/40 | 7/7 |
| glm-ocr | 1.4 | whole | 0/40 | 0/40 | 0/7 |
| glm-ocr | 1.4 | focus | 8/40 | 1/40 | 1/7 |

### What this says

- **Lightest model worth using: Qwen3-VL-4B (3.0 GB),** the current default. With
  the focus area 38/40 boxes land on the right line and 34/40 prices are exact.
  Qwen3-VL-8B is a little better on prices (38/40) at twice the size.
- **Qwen3-VL-2B (1.6 GB) is too inaccurate on prices** (23/40 even with focus); its
  boxes are fine with focus (34/40).
- **Gemma 4 shifts prices by one row** on receipts that print the name above its
  quantity/price line (12B: "pivo velke" gets "special 0,5l"'s 3,00 and so on). The
  sum still matches, so the total check can't catch it. E2B/E4B boxes are poor.
  Gemma needs thinking off (`--reasoning off`): with it on, E4B took 15 s per call and
  ran out of tokens on the supermarket receipt.
- **GLM-OCR is not a drop-in:** it reads text but doesn't return item boxes; with the
  JSON prompt it found almost nothing.
- **Mistral OCR** is API-only and needs a key; not run.
- **The app's validator** drops a whole answer for one coordinate of 1001..1010 at the
  crop edge, which costs focus mode the supermarket receipt for every Qwen size.
- 8 receipts, 73 items: enough to rank models, not to quote percentages.
