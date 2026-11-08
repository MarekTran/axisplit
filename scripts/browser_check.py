#!/usr/bin/env python
"""Browser-level verification of the token board (tap-on-photo snapping).

Headless Firefox (system binary + nixpkgs geckodriver) drives two real
sessions on one receipt link: a tap far below all boxes snaps to the nearest;
a placement in the second browser arrives in the first over the WebSocket; pip removal.

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
        r = c.post("/receipts", files={"image": ("check.jpg", f, "image/jpeg")})
    rid = r.url.path.split("/")[-1]
    for name in ("Adam", "Nina"):
        c.post(f"/r/{rid}/people", data={"username": name})
    for label, price in [("GrillFor2", "20,00"), ("Nachos", "5"), ("Beer x2", "2")]:
        c.post(f"/r/{rid}/items", data={"label": label, "price": price})
    # second receipt WITHOUT items -> exercises tap-to-add
    with open(img, "rb") as f:
        r = c.post("/receipts", files={"image": ("check2.jpg", f, "image/jpeg")})
    rid_empty = r.url.path.split("/")[-1]
    return rid, rid_empty


def open_page():
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service
    opts = Options()
    opts.add_argument("--headless")
    d = webdriver.Firefox(service=Service(), options=opts)
    d.set_window_size(500, 1300)
    return d


def main() -> int:
    subprocess.run(["rm", "-f", str(ROOT / "data" / "splits.db")], check=False)
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
        rid, rid_empty = seed()

        d1 = open_page(); drivers.append(d1)
        d1.get(f"{B}/r/{rid}")
        wait_for(lambda: len(d1.find_elements("css selector", ".box[data-item]")) == 3, "3 boxes")  # not the editor's draft

        # 1. Pick Adam, then tap near the BOTTOM of the photo (far from every synthetic box)
        d1.find_element("css selector", '.token[data-user="Adam"]').click()
        rect = d1.execute_script("const r=document.querySelector('#imgwrap img').getBoundingClientRect();"
                                 "return [r.x, r.y, r.width, r.height];")
        tap(d1, rect[0] + rect[2] * 0.5, rect[1] + rect[3] * 0.92)
        wait_for(lambda: d1.find_elements("css selector", ".box .pip[data-user='Adam']"),
                 "Adam pip via far tap")
        print("tap-far-snaps-to-nearest: OK")

        # 2. A second browser with the same link picks Nina and taps the middle of the first box;
        #    the first page must receive the pip over the WebSocket
        d2 = open_page(); drivers.append(d2)
        d2.get(f"{B}/r/{rid}")
        d2.find_element("css selector", '.token[data-user="Nina"]').click()
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

        # 4. tap-to-add on an empty receipt: tap where a line is printed ->
        #    mini form -> submit -> box centered on the tap point exists
        d1.get(f"{B}/r/{rid_empty}")
        wait_for(lambda: len(d1.find_elements("css selector", ".box")) == 0, "empty receipt")
        rect = d1.execute_script("const r=document.querySelector('#imgwrap img').getBoundingClientRect();"
                                 "return [r.x, r.y, r.width, r.height];")
        tap(d1, rect[0] + rect[2] * 0.4, rect[1] + rect[3] * 0.6)
        wait_for(lambda: d1.find_elements("css selector", "#quickadd"), "quick-add form")
        d1.find_element("css selector", "#quickadd input[name=label]").send_keys("Mala Chili mix")
        d1.find_element("css selector", "#quickadd input[name=price]").send_keys("1,00")
        d1.find_element("css selector", "#quickadd button[type=submit]").click()
        wait_for(lambda: len(d1.find_elements("css selector", ".box")) == 1, "box after tap-to-add")
        box = d1.execute_script("const b=document.querySelector('.box').getBoundingClientRect();"
                                "const i=document.querySelector('#imgwrap img').getBoundingClientRect();"
                                "return [((b.x+b.width/2)-i.x)/i.width, ((b.y+b.height/2)-i.y)/i.height];")
        print(f"tap-to-add: OK (box center at ~{box[0]:.2f},{box[1]:.2f} of image, tapped 0.40,0.60)")
        return 0
    finally:
        for d in drivers:
            d.quit()
        proc.terminate(); proc.wait()


if __name__ == "__main__":
    sys.exit(main())
