# OCR benchmarks

The measurements behind the OCR pipeline in [spec §5](spec.md#5-ocr-pipeline). For how to
run the scorer and the ground-truth format, see [`bench/README.md`](../bench/README.md).

All runs are from 2026-11-05:
- **CPU work:** a Ryzen 7 7800X3D (8 cores, 16 threads).
- **Models:** one Radeon RX 9070 XT (16 GB), through llama.cpp's Vulkan backend from the flake.

## Summary

- **Items: PP-OCRv6 small plus `receipt_rules`, on the focus area.**
  - Of everything tested on unseen receipts, it had the most exact prices and boxes.
  - It runs on the CPU in a fraction of a second.
- **Total: vllm-d3-nano picks it from the OCR's amount lines.** This goes through `/v1/systemone`, as text only.
  - On unseen receipts it got 17/19 totals right, against 11/19 for the rules alone.
  - It takes 0.10 s. Bigger decision models tied with it.
- **Vision models (Qwen3-VL):**
  - They read the total better: 18/19.
  - They got far fewer prices and boxes right, at 7–10 s per receipt.
  - They remain available as `AXISPLIT_OCR_BACKEND=vision`.
- **What to trust: the add-up check, not model confidence.** When the items added up to the picked total, the whole receipt was right on 23 of 24 receipts, including all 10 held out.
- **Open: the detector's input size.** The shipped 1280 px cap costs prices on the public photos; see [Detector input size](#detector-input-size).

## Data

| set | receipts | items | where | used for |
|---|---|---|---|---|
| public | 8 | 73 | ground truth in `bench/cases/`, photos in `test_photos/` | ranking vision models, and reproducible checks |
| private | 38: 19 tune + 19 held out | 346 + 244 | the maintainer's receipts, not in the repo | an honest test of the rules |

- **Public set:** the rules were written while looking at these 8 receipts, so OCR-plus-rules scores on it are optimistic.
- **Private set:** only aggregate numbers are published here.
  - The rules were tuned on the tune half and frozen (v2).
  - Then the held-out half was scored once.
- **Box scores favour OCR on the private set.** Its labelling tool pre-filled the OCR's text lines, so the ground-truth quads come from the same detector.
- **The held-out half is now spent.** Its failures have been looked at, so the next round of rule changes needs fresh receipts to test against.

## Columns

- **items_found:** ground-truth items matched by name, ignoring a quantity prefix and accents, or by the same price with the prediction's centre on the item's line.
- **price_right:** matched, with the exact price.
- **box_on_line:** matched, and the prediction's centre lies inside the item's quad, so a tap there hits the right item. OCR pipelines are judged by their own quads.
- **mean_iou:** overlap of the predicted box with the quad.
- **extra_items:** predictions that match nothing.
- **sum_exact:** receipts whose predicted prices add up to the items' total.
- **total_right:** the printed total read correctly.
- **failed_calls:** answers the app rejects: a box coordinate outside 0..1000, or an answer cut off at 4096 tokens.
- **median_s:** seconds per items call.
- **size_gb:** the model file plus its vision projector.

Modes:
- **whole:** items read from the whole photo.
- **focus:** items read from the area a user would drag, taken as the ground-truth items' extent plus 1%.

## Vision models, public set

Each model ran through the app's vision code (`bench/vlm.py`: same prompt and llama-server
flags as `axisplit-ocr`), with llama.cpp build 11960 and Q4 quants. The totals are a
separate call on the whole photo.

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

- **Lightest model worth using: Qwen3-VL-4B (3.0 GB),** the vision backend's default. With
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

## OCR plus rules, public set

PP-OCRv6 through RapidOCR on the CPU, then `receipt_rules`. In these runs the detector saw each image at RapidOCR's default size (not the app's 1280 px cap) and ONNX Runtime used its default thread count. Totals are the rules' own.

| pipeline | mode | items_found | price_right | box_on_line | extra_items | sum_exact | total_right | median_s |
|---|---|---|---|---|---|---|---|---|
| PP-OCRv6 tiny + rules | whole | 62/73 | 62/73 | 57/73 | 5 | 5/8 | 6/8 | 0.3 |
| PP-OCRv6 tiny + rules | focus | 70/73 | 70/73 | 68/73 | 1 | 5/8 | 6/8 | 0.2 |
| PP-OCRv6 small + rules | whole | 66/73 | 64/73 | 63/73 | 0 | 7/8 | 8/8 | 0.5 |
| PP-OCRv6 small + rules | focus | 71/73 | 71/73 | 70/73 | 0 | 7/8 | 8/8 | 0.3 |
| PP-OCRv6 medium + rules | whole | 70/73 | 68/73 | 69/73 | 0 | 7/8 | 8/8 | 1.3 |
| PP-OCRv6 medium + rules | focus | 73/73 | 73/73 | 73/73 | 0 | 8/8 | 8/8 | 0.7 |

Model files are 6 MB (tiny), 31 MB (small) and 140 MB (medium). They use about 0.45 GB, 0.5 GB and 1 GB of RAM.

**Rotation.** The photos were rotated and the run repeated (small, focus):
- **−7°:** 72/73 prices right.
- **+7°:** 56/73 prices right. This direction pushes already-tilted photos to about 10°.
- **90°:** 0/73. Sideways photos need an orientation check first; EXIF handling covers normal phone photos.

## Private set: OCR plus rules vs vision models

The detector was capped at 1280 px, as in the app. The OCR rows' totals are the rules' own; the decision model's picks are in [Totals](#totals-decision-models).

**Held-out half (19 receipts, 244 items):**

| pipeline | mode | items_found | price_right | box_on_line | extra_items | sum_exact | total_right | failed_calls |
|---|---|---|---|---|---|---|---|---|
| PP-OCRv6 small + rules | whole | 204/244 | 199/244 | 200/244 | 16 | 7/19 | 11/19 | 0 |
| **PP-OCRv6 small + rules** | **focus** | **220/244** | **219/244** | **215/244** | **1** | **11/19** | 11/19 | 0 |
| PP-OCRv6 medium + rules | whole | 198/244 | 194/244 | 189/244 | 11 | 8/19 | 13/19 | 0 |
| PP-OCRv6 medium + rules | focus | 217/244 | 210/244 | 211/244 | 1 | 10/19 | 13/19 | 0 |
| Qwen3-VL-2B | whole | 206/244 | 96/244 | 53/244 | 51 | 2/19 | 17/19 | 2 |
| Qwen3-VL-2B | focus | 141/244 | 59/244 | 107/244 | 14 | 3/19 | 17/19 | 5 |
| Qwen3-VL-4B | whole | 236/244 | 178/244 | 115/244 | 16 | 6/19 | 18/19 | 0 |
| Qwen3-VL-4B | focus | 231/244 | 175/244 | 174/244 | 20 | 8/19 | 18/19 | 0 |
| Qwen3-VL-8B | whole | 238/244 | 176/244 | 108/244 | 9 | 9/19 | 18/19 | 0 |
| Qwen3-VL-8B | focus | 145/244 | 105/244 | 94/244 | 8 | 8/19 | 18/19 | 5 |

**Tune half (19 receipts, 346 items):**

| pipeline | mode | items_found | price_right | box_on_line | extra_items | sum_exact | total_right | failed_calls |
|---|---|---|---|---|---|---|---|---|
| PP-OCRv6 small + rules | whole | 343/346 | 343/346 | 343/346 | 12 | 13/19 | 17/19 | 0 |
| PP-OCRv6 small + rules | focus | 340/346 | 339/346 | 339/346 | 6 | 17/19 | 17/19 | 0 |
| PP-OCRv6 medium + rules | whole | 323/346 | 323/346 | 323/346 | 10 | 15/19 | 18/19 | 0 |
| PP-OCRv6 medium + rules | focus | 340/346 | 339/346 | 338/346 | 2 | 14/19 | 18/19 | 0 |
| Qwen3-VL-2B | whole | 277/346 | 204/346 | 90/346 | 16 | 2/19 | 17/19 | 3 |
| Qwen3-VL-2B | focus | 167/346 | 117/346 | 112/346 | 7 | 1/19 | 17/19 | 8 |
| Qwen3-VL-4B | whole | 310/346 | 259/346 | 182/346 | 15 | 9/19 | 16/19 | 0 |
| Qwen3-VL-4B | focus | 325/346 | 262/346 | 192/346 | 11 | 8/19 | 16/19 | 1 |
| Qwen3-VL-8B | whole | 291/346 | 246/346 | 125/346 | 16 | 6/19 | 17/19 | 1 |
| Qwen3-VL-8B | focus | 183/346 | 167/346 | 137/346 | 9 | 10/19 | 17/19 | 6 |

**Seconds per items call, all 38 receipts:**

| pipeline | mode | median | p90 | max | failed calls |
|---|---|---|---|---|---|
| PP-OCRv6 small + rules | whole | 0.67 | 0.81 | 0.97 | – |
| PP-OCRv6 small + rules | focus | 0.39 | 0.63 | 0.78 | – |
| PP-OCRv6 medium + rules | whole | 1.91 | 2.62 | 4.22 | – |
| PP-OCRv6 medium + rules | focus | 1.08 | 1.99 | 2.56 | – |
| Qwen3-VL-2B | whole | 4.45 | 12.70 | 19.80 | 4 cut off, 1 box > 1000 |
| Qwen3-VL-2B | focus | 4.15 | 7.50 | 20.00 | 11 box > 1000, 2 cut off |
| Qwen3-VL-4B | whole | 7.50 | 14.80 | 25.10 | – |
| Qwen3-VL-4B | focus | 7.60 | 13.70 | 24.90 | 1 box > 1000 |
| Qwen3-VL-8B | whole | 9.75 | 16.90 | 29.60 | 1 box > 1000 |
| Qwen3-VL-8B | focus | 9.90 | 17.00 | 27.70 | 11 box > 1000 |

The OCR times here are from before the CPU-thread fix in [The app](#the-app-paddlebackend), so they're about 1.6× slower than the app's.

What the private set says:
- **Prices:** OCR plus rules with the focus area gets 90% of unseen prices exactly right (219/244), against 73% for Qwen3-VL-4B (178/244).
- **Extra items:** OCR plus rules adds one extra item on unseen receipts; Qwen3-VL-4B adds 16–20.
- **Boxes:** OCR plus rules puts 215/244 boxes on the right line, against 174/244 for Qwen3-VL-4B at best.
- **Totals:** this is the vision models' one strength (18/19), which led to the decision model below.
- **Medium detector:** it reads unseen items no better than small, and misses the 1 s budget.
- **Bigger Qwen:** going from 4B to 8B buys nothing here.

## Totals: decision models

The question is which labelled amount line holds the total. Its wording is `ocr.DECIDE_TOTAL`. The candidates are every positive amount line from the whole-photo OCR, sent as one `choice` question to llama-server's `/v1/systemone`. Each model ran alone on the GPU, unless the table says CPU.

| how the total is found | params | file | held-out | tune | median_s, held-out |
|---|---|---|---|---|---|
| rules only | | | 11/19 | 17/19 | – |
| Qwen3-VL-4B, a separate vision call on the photo | | 3.0 GB | 18/19 | 16/19 | several s |
| **vllm-d3-nano, OCR text (in the app)** | 2.4B | 2.6 GB | **17/19** | 17/19 | **0.10** |
| vllm-d3-nano, OCR text + photo | 2.4B | 2.9 GB | 17/19 | 17/19 | 0.69 |
| vllm-d3-lite, OCR text (bundled) | 1.0B | 1.1 GB | 17/19 | 15/19 | on the CPU: below |
| vllm-d3-edge, OCR text | 0.75B | 0.8 GB | 14/19 | 16/19 | 0.04 |
| vllm-d3-edge, OCR text, on the CPU | 0.75B | 0.8 GB | 14/19 | – | 0.67 |
| vllm-d3-edge, OCR text + photo | 0.75B | | 16/19 | 17/19 | 0.30 |
| vllm-d3-mini, OCR text | 4.8B | 5.2 GB | – | 16/19 | 0.24 (tune) |
| vllm-d3-flash (Q4), OCR text | 9.0B | 6.6 GB | 17/19 | 18/19 | 0.30 |
| vllm-d3-flash (Q4), OCR text + photo | 9.0B | | 17/19 | 18/19 | 1.48 |
| lev, OCR text | 4.2B | 4.5 GB | 17/19 | 17/19 | 0.36 |
| Kev-4B, OCR text | 4.2B | 4.5 GB | 17/19 | 17/19 | 0.59 |
| Qwen3-VL-4B as a picker (answer-letter probabilities) | 4B | 3.0 GB | 17/19 | 17/19 | 0.10 |
| Julia-1, OCR text | 144M | | – | 9/19 | 0.04 (tune) |
| Laya, OCR text | 421M | | – | 3/19 | 0.05 (tune) |
| clef-vision-0.8b, OCR text, on the CPU | 0.8B | | – | 7/19 | 3.08 (tune) |
| clef-vision-0.8b, photo, on the CPU | 0.8B | | – | 15/19 | 3.38 (tune) |

- **Size doesn't help.** Every model from nano up gets the same 17 held-out receipts right.
- **The held-out ceiling is 17/19.** On the other 2, the right total isn't among the OCR's amount lines, so no picker can choose it:
  - on one, a space inside the amount and a misread currency sign hide it from the parser;
  - on the other, the total line wasn't read at all.
- **Where gains are left:** reading the receipt, not picking from it.
- **Size helped on one receipt of 38:** flash got one tune receipt that nano missed.
- **The photo doesn't help nano,** and makes it 7× slower. Without the photo, `axisplit-decide` loads no vision projector, which saves 365 MB.
- **Confidence isn't a usable gate.**
  - Across both halves, nano was right on 27 of the 30 picks it gave a confidence of 0.5–0.9.
  - Qwen's picker was wrong on three picks it gave 0.94 or more.
- **The add-up check is usable.** When the focus-area items added up to nano's pick, the whole receipt was right on 10 of 10 held-out receipts and 13 of 14 tune receipts. The exception had two misread items whose errors cancelled out.

### On the CPU, end to end

The model runs on the CPU next to the app by default, so it was chosen on upload times there. Each upload went through the app (`POST` with the photo and the items area, until the redirect), on all 46 labelled receipts, then 10 pairs of uploads at once. The model is llama.cpp's Vulkan build with `--device none`: the plain build links BLAS, and made the model 1.5× slower here. lite ran as bundled, with `-ub 4096`; the others with `-ub 8192`. Ryzen 7 7800X3D; "4 CPUs" is the app and the model pinned to 4 of its cores with `taskset`.

| CPUs | totals from | median s | p90 | slowest | slowest of two at once | first upload | model RAM | model ready |
|---|---|---|---|---|---|---|---|---|
| 8 cores | rules only | 0.76 | 1.21 | 1.32 | 1.96 | 0.98 | – | – |
| 8 cores | vllm-d3-nano | 4.33 | 6.46 | 8.60 | 12.0 | 3.42 | 3.3 GB | 2.8 s |
| 8 cores | **vllm-d3-lite** | **2.34** | 3.58 | 5.60 | 8.10 | 1.89 | 1.6 GB | 1.5 s |
| 8 cores | vllm-d3-edge | 1.55 | 2.44 | 3.03 | 4.26 | 1.52 | 1.5 GB | 1.3 s |
| 4 CPUs | rules only | 1.66 | 2.78 | 3.14 | 4.35 | 1.43 | – | – |
| 4 CPUs | vllm-d3-nano | 8.34 | 12.97 | 16.25 | 23.45 | 5.83 | 3.3 GB | 2.7 s |
| 4 CPUs | **vllm-d3-lite** | **4.60** | 7.17 | 8.96 | 12.48 | 3.22 | 1.6 GB | 1.4 s |
| 4 CPUs | vllm-d3-edge | 3.07 | 4.96 | 6.01 | 8.06 | 3.05 | 1.6 GB | 1.3 s |

- **lite is bundled.** It gets nano's 17 of 19 unseen totals right in about half the time and RAM. On the tune half it gets 2 fewer, on a question worded with nano. edge is faster still, but gets 3 fewer unseen totals right.
- **The app** peaks at 0.6–0.8 GB. "First upload" includes loading the OCR models; "model ready" is from starting the model, its file evicted from the page cache, to its health check passing.
- **Threads.** On 4 CPUs, the model ran 5% slower with 8 threads than with 4, so it takes one per CPU it may use. The OCR used to count the host's CPUs: pinned to 4 of 16, it ran 8 threads on them and took 3.4 s per upload with rules alone. It now counts the CPUs it may use (1.7 s).
- **Two at once** wait for each other: one OCR engine and one model slot.
- **The deadline** (`AXISPLIT_DECIDE_TIMEOUT`, 15 s) is above the slowest upload on 4 CPUs. A slower machine takes the rules' total past it.
- **A 4 GB machine:** at `-ub 8192`, lite reserves 6.3 GB of address space for its graph (it touches 1.8 GB), which a 4 GB machine without swap refuses: the server aborts on start. The longest question here is 2,176 tokens, so it runs with `-ub 4096`: 3.2 GB reserved, 1.6 GB used.

### Per-item check (not in the app)

A second decision question per item: "is this a real purchased line?", with a 0.5 threshold, on whole-photo items.

| half | extra items, before → after | items_found, before → after | median s per receipt |
|---|---|---|---|
| held out | 16 → 8 | 204 → 204 | 0.57 |
| tune | 12 → 4 | 343 → 341 | 1.05 |

The focus area alone gets extra items down to 1 for free, so this is only worth it for uploads without one. vllm-d3-edge as the checker removed almost no extra items and dropped real ones.

## The app (`PaddleBackend`)

**It matches the benchmark exactly.** On all 38 private receipts, the app's items and total picks are identical to the benchmark's.

**Time per upload**, all 38 private receipts: focus-area OCR, whole-photo OCR and the decision call. The three stage columns are medians.

| median | p90 | max | focus OCR | whole OCR | decision |
|---|---|---|---|---|---|
| 0.96 s | 1.39 s | 1.53 s | 0.34 s | 0.50 s | 0.12 s |

- **CPU threads:** ONNX Runtime gets one thread per physical core (`cpu_count // 2`). That's 0.95 s per upload, against 1.53 s with all 16 SMT threads, and the text read is identical.
- **No parallel OCR:** running the two OCR passes in parallel was 1.8× slower (2.34 s against 1.32 s), so they run one after the other.

**Through HTTP, public set:** the 8 photos uploaded to a running app, with the focus area from the labels.

| backend | items add up to the total | seconds per upload |
|---|---|---|
| vision (Qwen3-VL-4B, before #9) | 4/8, one failed | 2.9–13.9 |
| `PaddleBackend` | 6/8 | 0.4–1.0 |

Both `PaddleBackend` misses are flagged by the add-up check:
- **Focus area too generous:** on one receipt it included the total line, which OCR misread as a keyword the rules don't know. That line became an item.
- **Subtotal picked:** on the supermarket receipt, nano took the subtotal over the amount after discounts. Discount lines aren't saved as items yet.

### Detector input size

The app caps the detector's input at 1280 px on the longer side (`Det.limit_type=max`). On the private tune half that cap:
- improved whole-photo prices from 328/346 to 343/346;
- cost one focus-area price (340 → 339);
- roughly halved the OCR time.

On the public set, rerun through the app's code with nano for the total, the same cap costs prices:

| detector input | whole: price_right | whole: sum_exact | focus: price_right | focus: extra | focus: sum_exact | total_right | median s, whole / focus |
|---|---|---|---|---|---|---|---|
| **1280 px max (shipped)** | 49/73 | 6/8 | 66/73 | 1 | 6/8 | 7/8 | 0.3 / 0.1 |
| 1600 px max | 65/73 | 7/8 | 66/73 | 1 | 6/8 | 7/8 | 0.3 / 0.1 |
| 2048 px max | 64/73 | 7/8 | 66/73 | 1 | 6/8 | 8/8 | 0.5 / 0.2 |
| RapidOCR default (short side at least 736 px) | 64/73 | 7/8 | 71/73 | 0 | 7/8 | 8/8 | 0.4 / 0.2 |

- **Whole photo:** the loss on these photos is from downscaling the dense supermarket receipt; 1600 px recovers it.
- **Focus area:** the loss is the same at every cap. With `limit_type=max`, small crops are no longer enlarged, while the default enlarges them until the short side is 736 px. That is probably the cause.
- **A likely better setting:** cap the whole photo and enlarge small crops. This is untested on unseen receipts.

## Resources

- **llama.cpp's RAM prompt cache:** it never hits for receipts and only grows; it is 8 GB by default.
  - Qwen3-VL-4B's server reached 8.4 GB of RAM with it, and stayed at 1.2 GB with `--cache-ram 0`.
  - Both `axisplit-ocr` and `axisplit-decide` set `--cache-ram 0`.
- **GLM-OCR's vision encoder** fell back to the CPU and took 14 GB of RAM.
- **Model servers in the benchmark** run in a memory-capped systemd scope (`--max-ram-gb`).

## Not tested

- **Mistral OCR:** API-only and needs a key.
- **OpenJev (27B):** 16.5–19.4 GB at 4-bit, which doesn't fit a 16 GB card, and the licence is CC BY-NC 4.0. On held-out receipts it could at best match nano. Cloudflare's Clef 27B has the same size problem.
- **Jev:** hosted only, so receipts would leave the machine. It uses the same System One API, so `AXISPLIT_DECIDE_URL` could point at it.
- **CORD-v2** (about 1,000 public receipts with line boxes): the obvious next unseen set.
- **PaddleOCR's OpenVINO runtime:** said to be faster on CPUs.

## Next tuning

- The detector input size (above).
- Discount lines saved as items, so receipts with discounts can add up.
- Fuzzy total keywords, and amounts with a space as the thousands separator.
- Taking the rules' total when their items add up to it. That gains one tune receipt.
- Rotated boxes in the UI: the rules already compute each item's quad.
