"""The labelling and review tool for one dataset (see labels.py). Local only, standard library.

    python -m bench.label.server DATASET [PORT]      # then open http://127.0.0.1:8095

Saving as reviewed also writes cases/<id>/gt.json; a reviewed label must pass the checks, or say
in its note why its lines don't add up. When DATASET/labels-v1/ holds the labels from before a
relabelling, the page lists what changed in names, prices and the total.
"""

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import labels

DATASET = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
HERE = Path(__file__).resolve().parent
ID = r"([\w.-]+)"


def ids():
    return sorted(p.stem for p in (DATASET / "images").glob("*.jpg") if (DATASET / "prefill" / f"{p.stem}.json").exists())


def known(rid):
    """The receipt's id as found on disk, or None: an id from a URL never builds a path itself."""
    return {i: i for i in ids()}.get(rid)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        return json.loads(self.rfile.read(int(self.headers["Content-Length"])))

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/":
            return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if p == "/api/index":
            out = []
            for rid in ids():
                label = labels.load(DATASET, rid)
                flags = sum(1 for e in (label or {}).get("items", []) + [(label or {}).get("total") or {}] if e.get("flag"))
                out.append({"id": rid, "status": label["status"] if label else "todo", "flags": flags})
            return self.send(200, {"dataset": DATASET.name, "receipts": out})
        m = re.fullmatch(rf"/api/receipt/{ID}", p)
        rid = known(m[1]) if m else None
        if rid:
            saved, before = labels.load(DATASET, rid), DATASET / "labels-v1" / f"{rid}.json"
            changed = labels.changes(json.loads(before.read_text()), saved) if saved and before.exists() else []
            return self.send(200, {"prefill": labels.prefill(DATASET, rid), "saved": saved, "changes": changed})
        m = re.fullmatch(rf"/img/{ID}\.jpg", p)
        rid = known(m[1]) if m else None
        if rid:
            return self.send(200, (DATASET / "images" / f"{rid}.jpg").read_bytes(), "image/jpeg")
        self.send(404, {"error": "not found"})

    def do_POST(self):
        m = re.fullmatch(rf"/api/(check|receipt)/{ID}", self.path)
        rid = known(m[2]) if m else None
        if not rid:
            return self.send(404, {"error": "not found"})
        label = self.body()
        label["id"] = rid
        pre = labels.prefill(DATASET, rid)
        problems = labels.check(label, pre)
        if m[1] == "check":
            return self.send(200, {"problems": problems})
        if label.get("status") == "reviewed" and problems:
            return self.send(409, {"problems": problems})
        labels.save(DATASET, label)
        gt = DATASET / "cases" / rid / "gt.json"
        if label.get("status") == "reviewed":
            gt.parent.mkdir(parents=True, exist_ok=True)
            gt.write_text(json.dumps(labels.to_gt(label, pre, f"../../images/{rid}.jpg"), ensure_ascii=False, indent=1))
        else:
            gt.unlink(missing_ok=True)
        self.send(200, {"ok": True, "problems": problems})


if __name__ == "__main__":
    if DATASET is None:
        raise SystemExit(__doc__)
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8095
    print(f"Labelling {DATASET} on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
