"""Benchmark local vision models through the app's own OCR path (axisplit.ocr).

Each model gets its own llama-server, started and stopped here, on one GPU. Per receipt it is
asked three times, exactly as the app asks: items from the whole photo, items from the focus
area a user would drag (the GT items' extent plus a margin), and the printed total.

  # run (models download on first use into ~/.cache/axisplit-models)
  python -m bench.vlm run --models qwen3-vl-4b gemma-4-e4b glm-ocr \\
      --llama-server "$(grep -o '/nix/store/[^ ]*/bin/llama-server' "$(which axisplit-ocr)")" --device Vulkan1
  # score everything in bench/preds/vlm/ against the quads in bench/cases/*/gt.json
  python -m bench.vlm score
"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import httpx

from axisplit import ocr

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "cases"
PREDS = ROOT / "preds" / "vlm"
CACHE = Path(os.environ.get("AXISPLIT_MODEL_CACHE", Path.home() / ".cache" / "axisplit-models"))

# "box": order of the 4 numbers the model is trained to write. Gemma writes [y1, x1, y2, x2].
# Gemma attends over all image tokens at once, so one physical batch (-ub) must hold the whole image.
# Gemma 4 thinks before answering by default; the app wants the answer, so thinking is off
# ("-thinking" keeps it on, for comparison: slower, and long receipts run out of output tokens).
MODELS = {
    "qwen3-vl-2b": {"hf": "Qwen/Qwen3-VL-2B-Instruct-GGUF:Q4_K_M"},
    "qwen3-vl-4b": {"hf": "Qwen/Qwen3-VL-4B-Instruct-GGUF:Q4_K_M"},
    "qwen3-vl-8b": {"hf": "Qwen/Qwen3-VL-8B-Instruct-GGUF:Q4_K_M"},
    "gemma-4-e2b": {"hf": "ggml-org/gemma-4-E2B-it-GGUF:Q4_0", "box": "yxyx", "args": ["-ub", "2048", "--reasoning", "off"]},
    "gemma-4-e4b": {"hf": "ggml-org/gemma-4-E4B-it-GGUF:Q4_0", "box": "yxyx", "args": ["-ub", "2048", "--reasoning", "off"]},
    "gemma-4-e4b-thinking": {"hf": "ggml-org/gemma-4-E4B-it-GGUF:Q4_0", "box": "yxyx", "args": ["-ub", "2048"]},
    "gemma-4-12b": {"hf": "ggml-org/gemma-4-12B-it-GGUF:Q4_0", "box": "yxyx", "args": ["-ub", "2048", "--reasoning", "off"]},
    # GLM-OCR's image encoder attends over every patch on the CPU (unsupported on Vulkan at this
    # size): a 1600 px photo took 14 GB of RAM. Capping its image tokens keeps it near 2 GB.
    "glm-ocr": {"hf": "ggml-org/GLM-OCR-GGUF:Q8_0", "args": ["--flash-attn", "off", "--image-max-tokens", "1024"]},
}
# Same server flags as the app's axisplit-ocr (flake.nix), so results match what the app gets.
APP_ARGS = ["--ctx-size", "8192", "--parallel", "1", "--n-gpu-layers", "99", "--image-min-tokens", "1024"]
# Plus no RAM prompt cache: it defaults to 8 GB, never hits (every photo differs) and only grows.
# Doesn't change answers. Qwen3-VL-4B reached 8.4 GB of RAM without this.
NO_CACHE = ["--cache-ram", "0"]


class Backend(ocr.LocalVisionBackend):
    """The app's backend; only the box order in the prompt differs per model family."""

    def __init__(self, box="xyxy"):
        self.box = box

    def _ask(self, im, prompt, schema):
        if self.box == "yxyx" and schema is ocr.Detection:
            prompt = prompt.replace('"bbox":[x1,y1,x2,y2]', '"bbox":[y1,x1,y2,x2]')
            result = super()._ask(im, prompt, schema)
            for item in result.items:
                y1, x1, y2, x2 = item.bbox
                item.bbox = [x1, y1, x2, y2]
            return result
        return super()._ask(im, prompt, schema)


ONLY: set[str] | None = None  # case ids to use (--split FILE:KEY); None = all


def cases():
    return sorted(p for p in CASES.iterdir() if (p / "gt.json").exists() and "total" in json.loads((p / "gt.json").read_text())
                  and (ONLY is None or p.name in ONLY))


def focus_from_gt(gt, margin=0.01):
    """What a user would drag: the item lines and their prices, a little loose."""
    boxes = [i["bbox"] for i in gt["items"]]
    x1, y1 = max(0, min(b[0] for b in boxes) - margin), max(0, min(b[1] for b in boxes) - margin)
    x2, y2 = min(1, max(b[2] for b in boxes) + margin), min(1, max(b[3] for b in boxes) + margin)
    return (x1, y1, x2 - x1, y2 - y1)


def serve(name, llama, port, device, log, max_ram_gb):
    spec = MODELS[name]
    cmd = [llama, "-hf", spec["hf"], "--host", "127.0.0.1", "--port", str(port), *APP_ARGS, *NO_CACHE,
           "--device", device, *spec.get("args", [])]
    if max_ram_gb:  # a model that outgrows this is killed alone instead of the whole desktop
        cmd = ["systemd-run", "--user", "--scope", "--quiet", "-p", f"MemoryMax={max_ram_gb}G",
               "-p", "MemorySwapMax=0", "--", *cmd]
    proc = subprocess.Popen(cmd, env=dict(os.environ, HF_HOME=str(CACHE)), stdout=log, stderr=subprocess.STDOUT)
    deadline = time.time() + 3600  # first use downloads the model
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{name}: llama-server exited ({proc.returncode}); see {log.name}")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(2)
    proc.terminate()
    raise RuntimeError(f"{name}: server not ready within an hour")


def timed(fn, *args):
    start = time.time()
    try:
        return fn(*args), round(time.time() - start, 1), None
    except Exception as e:  # a failed call is a result too: 0 items
        return None, round(time.time() - start, 1), f"{type(e).__name__}: {e}"[:300]


def run(args):
    for name in args.models:
        out = PREDS / name
        out.mkdir(parents=True, exist_ok=True)
        with open(out / "server.log", "w") as log:
            proc = serve(name, args.llama_server, args.port, args.device, log, args.max_ram_gb)
            try:
                os.environ["AXISPLIT_OCR_URL"] = f"http://127.0.0.1:{args.port}/v1"
                backend = Backend(MODELS[name].get("box", "xyxy"))
                for case in cases():
                    gt = json.loads((case / "gt.json").read_text())
                    image = (case / gt["image"]).resolve()
                    pf = out / f"{case.name}.json"
                    result = json.loads(pf.read_text()) if pf.exists() else {}
                    for mode, focus in (("whole", None), ("focus", focus_from_gt(gt))) if "items" in args.parts else ():
                        items, seconds, error = timed(backend.extract_items, image, focus)
                        result[mode] = {"seconds": seconds, "error": error, "items": [
                            {"label": i.label, "price_cents": i.price_cents,
                             "bbox": [round(i.x, 4), round(i.y, 4), round(i.x + i.w, 4), round(i.y + i.h, 4)]}
                            for i in items or []]}
                    if "total" in args.parts:
                        total, seconds, error = timed(backend.extract_total, image)
                        result["total"] = {"seconds": seconds, "error": error, "total_cents": total}
                    pf.write_text(json.dumps(result, indent=1, ensure_ascii=False))
                    print(f"{name} {case.name}: whole {len(result['whole']['items'])} items "
                          f"({result['whole']['seconds']}s), focus {len(result['focus']['items'])} "
                          f"({result['focus']['seconds']}s), total {result['total']['total_cents']}", flush=True)
            finally:
                proc.terminate(); proc.wait()


# ---------- scoring against the quads ----------

def norm(label):
    s = unicodedata.normalize("NFKD", label.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"^\s*\d+\s*(x|ks|×|\.)?\s+", "", s)  # leading quantity, e.g. "1 x", "2 ks"
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def label_sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    sim = SequenceMatcher(None, a, b).ratio()
    return max(sim, 0.9) if min(len(a), len(b)) >= 4 and (a in b or b in a) else sim


def area(poly):
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]))) / 2


def clip(subject, clipper):
    """Sutherland–Hodgman: subject polygon clipped by a convex polygon."""
    sign = 1 if sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(clipper, clipper[1:] + clipper[:1])) > 0 else -1
    def inside(p, a, b):
        return sign * ((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) >= 0
    def cross(p, q, a, b):
        x1, y1, x2, y2, x3, y3, x4, y4 = *p, *q, *a, *b
        d = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / d
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
    out = subject
    for a, b in zip(clipper, clipper[1:] + clipper[:1]):
        pts, out = out, []
        for p, q in zip(pts, pts[1:] + pts[:1]):
            if inside(q, a, b):
                if not inside(p, a, b):
                    out.append(cross(p, q, a, b))
                out.append(q)
            elif inside(p, a, b):
                out.append(cross(p, q, a, b))
        if not out:
            return []
    return out


def rect_poly(b):
    x1, y1, x2, y2 = b
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def quad_iou(bbox, quad):
    rect, quad = rect_poly(bbox), [tuple(p) for p in quad]
    inter = clip(rect, quad)
    i = area(inter) if len(inter) >= 3 else 0.0
    u = area(rect) + area(quad) - i
    return i / u if u > 0 else 0.0


def centre_in(bbox, quad):
    """Would a tap on the predicted box's centre land on the right printed line?"""
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    signs = [(b[0] - a[0]) * (cy - a[1]) - (b[1] - a[1]) * (cx - a[0]) for a, b in zip(quad, quad[1:] + quad[:1])]
    return all(s >= 0 for s in signs) or all(s <= 0 for s in signs)


def pred_centre_in(pred, quad):
    """A prediction with its own quad (OCR pipelines) is judged by that quad's centre."""
    if "quad" in pred:
        cx = sum(x for x, _ in pred["quad"]) / 4
        cy = sum(y for _, y in pred["quad"]) / 4
        return centre_in([cx, cy, cx, cy], quad)
    return centre_in(pred["bbox"], quad)


def pred_iou(pred, quad):
    if "quad" in pred:
        a, b = [tuple(p) for p in pred["quad"]], [tuple(p) for p in quad]
        inter = clip(a, b)
        i = area(inter) if len(inter) >= 3 else 0.0
        u = area(a) + area(b) - i
        return i / u if u > 0 else 0.0
    return quad_iou(pred["bbox"], quad)


def match(gt_items, pred):
    """By name (fuzzy), or, when the names differ (OCR noise, terse labels), by the same price
    with the prediction's centre inside the item's quad."""
    pairs = []
    for g, gi in enumerate(gt_items):
        for p, pi in enumerate(pred):
            sim = label_sim(gi["label"], pi["label"])
            same_price, on_line = pi["price_cents"] == gi["price_cents"], pred_centre_in(pi, gi["quad"])
            if sim >= 0.6 or (same_price and on_line):
                pairs.append((max(sim, 0.5) + 0.25 * same_price + 0.25 * on_line, g, p))
    used_g, used_p, out = set(), set(), []
    for _, g, p in sorted(pairs, reverse=True):
        if g not in used_g and p not in used_p:
            used_g.add(g); used_p.add(p); out.append((gt_items[g], pred[p]))
    return out


def size_gb(name):
    """Model file + vision projector on disk, as llama-server -hf downloaded them."""
    spec = MODELS.get(name)
    if not spec:
        return None
    repo, quant = spec["hf"].split(":")
    snaps = CACHE / "hub" / f"models--{repo.replace('/', '--')}" / "snapshots"
    files = {f.name: f.resolve().stat().st_size for f in snaps.glob("*/*.gguf")} if snaps.exists() else {}
    model = sum(s for n, s in files.items() if quant.lower() in n.lower() and not n.startswith(("mmproj", "mtp")))
    proj = max((s for n, s in files.items() if n.startswith("mmproj")), default=0)
    return round((model + proj) / 1e9, 1) if model else None


def score(args):
    rows = []
    for model_dir in sorted(p for p in PREDS.iterdir() if p.is_dir()):
        for mode in ("whole", "focus"):
            n_gt = n_pred = found = price_ok = on_line = sums_ok = totals_ok = errors = n_cases = 0
            ious, seconds = [], []
            for case in cases():
                pf = model_dir / f"{case.name}.json"
                if not pf.exists():
                    continue
                gt, pr = json.loads((case / "gt.json").read_text()), json.loads(pf.read_text())
                n_cases += 1
                pred = pr[mode]["items"]
                errors += bool(pr[mode]["error"])
                seconds.append(pr[mode]["seconds"])
                pairs = match(gt["items"], pred)
                n_gt += len(gt["items"]); n_pred += len(pred); found += len(pairs)
                price_ok += sum(p["price_cents"] == g["price_cents"] for g, p in pairs)
                on_line += sum(pred_centre_in(p, g["quad"]) for g, p in pairs)
                ious += [pred_iou(p, g["quad"]) for g, p in pairs]
                sums_ok += sum(p["price_cents"] for p in pred) == sum(g["price_cents"] for g in gt["items"])
                totals_ok += pr["total"]["total_cents"] == gt["total"]["price_cents"]
            if not n_cases:
                continue
            rows.append({
                "model": model_dir.name, "size_gb": size_gb(model_dir.name), "mode": mode, "receipts": n_cases,
                "items_found": f"{found}/{n_gt}", "price_right": f"{price_ok}/{n_gt}",
                "box_on_line": f"{on_line}/{n_gt}", "mean_iou": round(statistics.mean(ious), 2) if ious else 0,
                "extra_items": n_pred - found, "sum_exact": f"{sums_ok}/{n_cases}",
                "total_right": f"{totals_ok}/{n_cases}", "failed_calls": errors,
                "median_s": round(statistics.median(seconds), 1) if seconds else None,
            })
    if args.json:
        print(json.dumps(rows, indent=1))
        return
    head = ["model", "size_gb", "mode", "items_found", "price_right", "box_on_line", "mean_iou", "extra_items",
            "sum_exact", "total_right", "failed_calls", "median_s"]
    print("| " + " | ".join(head) + " |")
    print("|" + "---|" * len(head))
    for r in rows:
        print("| " + " | ".join(str(r[h]) for h in head) + " |")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--models", nargs="+", choices=sorted(MODELS), required=True)
    r.add_argument("--llama-server", default="llama-server")
    r.add_argument("--device", required=True, help="e.g. Vulkan1; see llama-server --list-devices")
    r.add_argument("--port", type=int, default=8090)
    r.add_argument("--max-ram-gb", type=float, default=10, help="host RAM cap per model server; 0 = none")
    r.add_argument("--parts", nargs="+", choices=["items", "total"], default=["items", "total"],
                   help="re-run only some calls; results merge into existing prediction files")
    s = sub.add_parser("score")
    s.add_argument("--json", action="store_true")
    for sp in (r, s):
        sp.add_argument("--cases", type=Path, help="cases dir (default bench/cases)")
        sp.add_argument("--preds", type=Path, help="predictions dir (default bench/preds/vlm)")
        sp.add_argument("--split", help="FILE:KEY, e.g. split.json:heldout, to use only those case ids")
    args = ap.parse_args()
    global CASES, PREDS, ONLY
    CASES, PREDS = args.cases or CASES, args.preds or PREDS
    if args.split:
        path, key = args.split.rsplit(":", 1)
        ONLY = set(json.loads(Path(path).read_text())[key])
    run(args) if args.cmd == "run" else score(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
