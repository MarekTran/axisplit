#!/usr/bin/env python
"""WS smoke: Adam's op must broadcast to Nina's socket (live token board)."""
import asyncio
import json
import os
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

import httpx
import websockets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
B = "http://127.0.0.1:8778"


def setup_receipt():
    c = httpx.Client(base_url=B, follow_redirects=True)
    img = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    from PIL import Image
    Image.new("RGB", (300, 400), "#eee").save(img.name)
    with open(img.name, "rb") as f:
        r = c.post("/receipts", files={"image": ("r.jpg", f, "image/jpeg")})
    rid = r.url.path.split("/")[-1]
    for name in ("Adam", "Nina"):
        c.post(f"/r/{rid}/people", data={"username": name})
    c.post(f"/r/{rid}/items", data={"label": "Beer", "price": "4"})
    r = c.get(f"/r/{rid}")
    item_id = r.text.split('data-item="')[1].split('"')[0]
    return c, rid, item_id


async def recv_type(ws, t, timeout=5):
    while True:
        ev = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if ev["type"] == t:
            return ev


async def main():
    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "axisplit.main:app", "--port", "8778"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen(B + "/"); break
            except Exception:
                time.sleep(0.25)
        _, rid, item_id = setup_receipt()

        async with websockets.connect(f"ws://127.0.0.1:8778/ws/receipts/{rid}") as adam, \
                   websockets.connect(f"ws://127.0.0.1:8778/ws/receipts/{rid}") as nina:
            # drain presence events
            await asyncio.wait_for(nina.recv(), 5)
            await adam.send(json.dumps({"op": "place", "item_id": item_id, "username": "Adam"}))
            ev = await recv_type(nina, "place")
            assert ev == {"type": "place", "item_id": item_id, "username": "Adam", "status": "pending_confirm"}, ev
            await nina.send(json.dumps({"op": "confirm", "username": "Adam"}))  # anyone ticks anyone off
            ev = await recv_type(adam, "confirm")
            assert ev == {"type": "confirm", "username": "Adam", "status": "settled"}, ev
        print("WS SMOKE OK")
    finally:
        proc.terminate(); proc.wait()


if __name__ == "__main__":
    asyncio.run(main())
