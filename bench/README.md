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
