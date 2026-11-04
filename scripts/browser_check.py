#!/usr/bin/env python
"""Browser-level verification of the token board (tap-on-photo snapping).

Headless Firefox (system binary + nixpkgs geckodriver) drives two real
sessions: Adam taps far below all boxes -> snaps to nearest; Nina's placement
in a second browser arrives on Adam's page over the WebSocket; pip removal.

Run inside nix develop (needs geckodriver + firefox on PATH):
  nix shell nixpkgs#geckodriver -c uv run --with selenium python scripts/browser_check.py
"""
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
B = "http://127.0.0.1:8779"
SHOTS = ROOT / "data" / "shots"


def wait_for(cond, what, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.15)
    raise AssertionError(f"timeout waiting for {what}")


def tap(d, x, y):
    from selenium.webdriver.common.action_chains import ActionChains
    ActionChains(d).move_by_offset(int(x), int(y)).click() \
        .move_by_offset(-int(x), -int(y)).perform()


def seed():
    c = httpx.Client(base_url=B, follow_redirects=True)
    c.post("/claim", data={"username": "Adam", "next": "/t"})
    r = c.post("/tabs", data={"name": "browser check"})
    tab = r.url.path.split("/")[-1]
    import re
    secret = re.search(r"\?s=([\w-]+)", c.get(f"/t/{tab}").text).group(1)
    n = httpx.Client(base_url=B, follow_redirects=True)
    n.post("/claim", data={"username": "Nina", "next": f"/t/{tab}?s={secret}"})

    img = SHOTS / "receipt_src.jpg"
    SHOTS.mkdir(parents=True, exist_ok=True)
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (400, 600), "#f3efe7")
    d = ImageDraw.Draw(im)
    d.text((20, 30), "CAFE AXIS  *  CHECK", fill="black")
    for i, line in enumerate(["GrillFor2   20,00", "Caesar Salad  10,00", "Nachos         5,00",
                              "Beer x2       2,00", "Cola Zero      2,00"]):
        d.text((20, 90 + i * 40), line, fill="black")
    im.save(img)
    with open(img, "rb") as f:
        r = c.post(f"/t/{tab}/receipts", files={"image": ("check.jpg", f, "image/jpeg")})
    rid = r.url.path.split("/")[-1]
    for label, price in [("GrillFor2", "20,00"), ("Nachos", "5"), ("Beer x2", "2")]:
        c.post(f"/r/{rid}/items", data={"label": label, "price": price})
    return rid, c.cookies.get("who"), n.cookies.get("who")


def open_page(cookie):
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service
    opts = Options()
    opts.add_argument("--headless")
    d = webdriver.Firefox(service=Service(), options=opts)
    d.set_window_size(500, 1300)
    d.get(B + "/")
    d.add_cookie({"name": "who", "value": cookie, "path": "/"})
    return d


def main() -> int:
    subprocess.run(["rm", "-f", str(ROOT / "data" / "axisplit.db")], check=False)
    proc = subprocess.Popen(["uv", "run", "uvicorn", "axisplit.main:app", "--port", "8779"],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    drivers = []
    try:
        for _ in range(60):
            try:
                httpx.get(B + "/", timeout=1)
                break
            except Exception:
                time.sleep(0.25)
        rid, adam_ck, nina_ck = seed()

        d1 = open_page(adam_ck); drivers.append(d1)
        d1.get(f"{B}/r/{rid}")
        wait_for(lambda: d1.find_elements("css selector", ".box") and len(d1.find_elements("css selector", ".box")) == 3, "3 boxes")

        # 1. Adam's own token is pre-selected on load; assert, then tap near
        #    the BOTTOM of the photo (far from every synthetic box)
        assert d1.find_elements("css selector", '.token[data-user="Adam"].selected'), \
            "own token should be pre-selected"
        rect = d1.execute_script("const r=document.querySelector('#imgwrap img').getBoundingClientRect();"
                                 "return [r.x, r.y, r.width, r.height];")
        tap(d1, rect[0] + rect[2] * 0.5, rect[1] + rect[3] * 0.92)
        wait_for(lambda: d1.find_elements("css selector", ".box .pip[data-user='Adam']"),
                 "Adam pip via far tap")
        print("tap-far-snaps-to-nearest: OK")

        # 2. Nina in a second browser taps the middle of the first box;
        #    Adam's page must receive the pip over the WebSocket
        d2 = open_page(nina_ck); drivers.append(d2)
        d2.get(f"{B}/r/{rid}")
        # (Nina's token is her own -> auto-selected; no tokenbar click needed)
        wait_for(lambda: d2.find_elements("css selector", '.token[data-user="Nina"].selected'),
                 "Nina token (pre-selected as her own)")
        b = d2.find_element("css selector", ".box")
        d2.execute_script("arguments[0].scrollIntoView({block:'center'})", b)
        r2 = d2.execute_script("const r=arguments[0].getBoundingClientRect(); return [r.x,r.y,r.width,r.height];", b)
        tap(d2, r2[0] + r2[2] / 2, r2[1] + r2[3] / 2)
        wait_for(lambda: d1.find_elements("css selector", ".box .pip[data-user='Nina']"),
                 "Nina pip appearing live on Adam's page (WS)")
        print("live-ws-cross-device: OK")

        # 3. removal: Adam taps his own pip
        d1.find_element("css selector", ".box .pip[data-user='Adam']").click()
        wait_for(lambda: not d1.find_elements("css selector", ".box .pip[data-user='Adam']"),
                 "Adam pip removed")
        print("pip-removal: OK")

        d1.get(f"{B}/r/{rid}")  # fresh load: server-rendered end state
        d1.save_screenshot(str(SHOTS / "board.png"))
        print(f"screenshot: {SHOTS / 'board.png'}")
        return 0
    finally:
        for d in drivers:
            d.quit()
        proc.terminate(); proc.wait()


if __name__ == "__main__":
    sys.exit(main())
