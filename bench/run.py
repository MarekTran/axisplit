"""Run a candidate OCR model over bench/cases/*/ and write preds/<case>.json.

Usage:
  uv run python -m bench.run --model stub --out bench/preds/stub
  uv run python -m bench.run --model qwen3-vl-8b --out bench/preds/qwen

VLM adapters talk to any OpenAI-compatible endpoint (LiteLLM proxy).
Env: AXISPLIT_LITELLM_URL (default http://localhost:4000/v1),
     AXISPLIT_LITELLM_KEY.
"""

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
from pathlib import Path

from .schema import load_gt

MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}

PROMPT = """You are a receipt parser. Return ONLY a JSON array, no prose, no markdown fences.
One entry per purchased line item on this restaurant/shop receipt:
[{"label": "<item name as printed>", "price_cents": <int, line total in cents, ignore currency>,
  "bbox": [x1, y1, x2, y2]}]
bbox coordinates are normalized 0..1 relative to the image (x1<x2, y1<y2) and must
tightly enclose the printed line for that item. Include quantity lines (e.g. "Beer x2"
with total price), exclude subtotal/tax/total/tip lines."""


def find_image(case: Path) -> Path:
    named = case / json.loads((case / "gt.json").read_text()).get("image", "")
    if named.is_file():  # e.g. ../../../test_photos/<id>.jpg, committed with the repo
        return named
    for ext in ("jpg", "jpeg", "png", "webp"):
        hits = sorted(case.glob(f"*.{ext}"))
        if hits:
            return hits[0]
    raise FileNotFoundError(f"no image in {case} (photos are gitignored; drop one in)")


def call_openai_compat(model: str, image: Path) -> list[dict]:
    from openai import OpenAI

    client = OpenAI(
        base_url=os.environ.get("AXISPLIT_LITELLM_URL", "http://localhost:4000/v1"),
        api_key=os.environ.get("AXISPLIT_LITELLM_KEY", "none"),
    )
    mime = MIME.get(image.suffix.lstrip(".").lower(), mimetypes.guess_type(image)[0] or "image/jpeg")
    data = base64.b64encode(image.read_bytes()).decode()
    resp = client.chat.completions.create(
        model=model,
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}},
            ],
        }],
    )
    text = resp.choices[0].message.content or ""
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        raise ValueError(f"no JSON array in response: {text[:200]!r}")
    return json.loads(match.group(0))


def run_stub(case: Path) -> list[dict]:
    """Plumbing smoke adapter: replays GT with slight box jitter.
    NOT a model result — proves score.py end to end."""
    out = []
    for g in load_gt(case):
        x1, y1, x2, y2 = g.bbox
        out.append({"label": g.label, "price_cents": g.price_cents,
                    "bbox": [x1 - 0.01, y1 - 0.01, x2 + 0.01, y2 + 0.01]})
    return out


ADAPTERS = {
    "stub": lambda case, img: run_stub(case),
    "dots.ocr": lambda case, img: call_openai_compat("dots.ocr", img),
    "qwen3-vl-8b": lambda case, img: call_openai_compat("Qwen/Qwen3-VL-8B-Instruct", img),
    "deepseek-ocr-2": lambda case, img: call_openai_compat("deepseek-ai/DeepSeek-OCR-2", img),
    "nanonets-ocr3": lambda case, img: call_openai_compat("nanonets/Nanonets-OCR-s", img),
    "unlimited-ocr-2": lambda case, img: call_openai_compat("baidu/Unlimited-OCR-2", img),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=sorted(ADAPTERS))
    ap.add_argument("--cases", default="bench/cases", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.list:
        print("\n".join(ADAPTERS))
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    adapter = ADAPTERS[args.model]
    for case in sorted(p for p in args.cases.iterdir() if (p / "gt.json").exists()):
        try:
            img = None if args.model == "stub" else find_image(case)
            items = adapter(case, img)
        except FileNotFoundError as e:
            print(f"skip {case.name}: {e}", file=sys.stderr)
            continue
        (args.out / f"{case.name}.json").write_text(json.dumps({"items": items}, indent=2))
        print(f"{case.name}: {len(items)} items -> {args.out}/{case.name}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
