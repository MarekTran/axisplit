"""UI regression checks with real Firefox sessions and an isolated temporary DB.

Inside nix develop:
  nix shell --inputs-from . nixpkgs#geckodriver -c \
    uv run --with selenium python scripts/ux_browser.py
Does not read or modify the running app's receipts. OCR is explicitly stubbed.
"""

import io
import os
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from PIL import Image
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8782"


def open_browser(cookie):
    options = Options()
    options.add_argument("--headless")
    driver = webdriver.Firefox(service=Service(), options=options)
    driver.set_window_size(1100, 1000)
    driver.get(BASE)
    driver.add_cookie({"name": "who", "value": cookie, "path": "/"})
    return driver


def wait(driver, condition):
    WebDriverWait(driver, 8).until(condition)


def holders(driver, item, user):
    return driver.find_elements("css selector", f'.holder[data-item="{item}"][data-user="{user}"]')


def place(driver, item, user):
    """Same path as a person: pick them in the token bar, then tap the item's row."""
    driver.find_element("css selector", f'#tokenbar .token[data-user="{user}"]').click()
    driver.find_element("css selector", f'[data-add="{item}"]').click()


def main():
    drivers = []
    with tempfile.TemporaryDirectory(prefix="axisplit-ux-") as data:
        env = dict(os.environ, AXISPLIT_DATA=data, AXISPLIT_OCR_BACKEND="stub")
        env.pop("DATABASE_URL", None)
        process = subprocess.Popen(
            [str(ROOT / ".venv/bin/uvicorn"), "axisplit.main:app", "--port", "8782"],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            client = httpx.Client(base_url=BASE, follow_redirects=True)
            for _ in range(60):
                try:
                    client.get("/")
                    break
                except httpx.ConnectError:
                    time.sleep(.2)
            client.post("/claim", data={"username": "Adam", "next": "/"})
            tab = client.post("/tabs", data={"name": "UX regression"}).url.path.rsplit("/", 1)[-1]
            client.post(f"/t/{tab}/members", data={"username": "Nina"})
            image = io.BytesIO()
            Image.new("RGB", (400, 600), "#f1eee5").save(image, "JPEG")
            page = client.post(f"/t/{tab}/receipts", files={"image": ("receipt.jpg", image.getvalue(), "image/jpeg")})
            rid = page.url.path.rsplit("/", 1)[-1]
            client.post(f"/r/{rid}/items", data={"label": "Mango tea", "price": "10", "x": ".5", "y": ".4"})
            client.post(f"/r/{rid}/items", data={"label": "Watermelon tea", "price": "20", "x": ".5", "y": ".6"})
            nina = httpx.Client(base_url=BASE, follow_redirects=True)
            nina.post("/claim", data={"username": "Nina", "next": "/"})
            adam = open_browser(client.cookies.get("who")); drivers.append(adam)
            peer = open_browser(nina.cookies.get("who")); drivers.append(peer)
            for driver in drivers:
                driver.get(f"{BASE}/r/{rid}")
            wait(adam, lambda d: d.find_elements("css selector", ".item-add"))
            item_ids = [i.get_attribute("data-item") for i in adam.find_elements("css selector", ".item-review li")]
            first, second = item_ids
            assert not adam.find_element("id", "confirm-btn").is_enabled()
            place(adam, first, "Adam")
            wait(adam, lambda d: holders(d, first, "Adam"))
            place(adam, second, "Nina")
            wait(adam, lambda d: d.find_element("id", "confirm-btn").is_enabled())
            wait(peer, lambda d: holders(d, second, "Nina"))
            adam.find_element("id", "confirm-btn").click()
            wait(adam, lambda d: "You agreed" in d.find_element("id", "review").text)
            wait(peer, lambda d: d.find_element("id", "confirm-btn").is_enabled())
            peer.find_element("id", "confirm-btn").click()
            wait(adam, lambda d: d.find_element("id", "status").text == "Everyone agreed")
            print("live assignment, shares, disabled confirmation and peer agreement: OK")

            holders(adam, first, "Adam")[0].click()
            wait(adam, lambda d: not holders(d, first, "Adam"))
            wait(adam, lambda d: d.find_element("id", "undo-token").is_displayed())
            adam.find_element("id", "undo-token").click()
            wait(adam, lambda d: holders(d, first, "Adam"))
            assert "You agreed" not in adam.find_element("id", "review").text
            print("token Undo restores assignment; confirmations reset: OK")

            # Four quick taps in one go: each is one more of Nina's tokens (4 parts vs Adam's 1).
            adam.find_element("css selector", '#tokenbar .token[data-user="Nina"]').click()
            adam.execute_script("for (let i = 0; i < 4; i++) document.querySelector(arguments[0]).click()",
                                f'[data-add="{first}"]')
            count = lambda d: (holders(d, first, "Nina") or [None])[0]
            wait(peer, lambda d: count(d) and count(d).get_attribute("data-count") == "4")
            wait(adam, lambda d: "28,00 €" in d.find_element("id", "review").text)  # 8 € of the 10 € + 20 €
            holders(adam, first, "Nina")[0].click()
            wait(peer, lambda d: count(d) and count(d).get_attribute("data-count") == "3")
            wait(peer, lambda d: "27,50 €" in d.find_element("id", "review").text)
            print("repeat taps stack tokens, shares follow parts, a chip takes one off: OK")

            # Duplicate upload using the actual file chooser, then the focus step ("Use whole photo").
            photo = Path(data) / "duplicate.jpg"
            photo.write_bytes(image.getvalue())
            adam.get(f"{BASE}/t/{tab}")
            adam.find_element("id", "receipt-image").send_keys(str(photo))
            wait(adam, lambda d: d.find_element("id", "focus-dialog").is_displayed())
            adam.find_element("css selector", '[data-focus="whole"]').click()
            wait(adam, lambda d: "already in the tab" in d.find_element("tag name", "main").text)
            assert adam.current_url.endswith(f"/r/{rid}?duplicate=1")
            print("duplicate upload reuses existing receipt: OK")

            adam.get(f"{BASE}/t/{tab}")
            adam.find_element("css selector", "[data-dialog]").click()
            assert adam.find_element("css selector", "dialog[open]").is_displayed()
            adam.find_element("css selector", "dialog[open] [data-close-dialog]").click()
            assert not adam.find_elements("css selector", "dialog[open]")
            adam.find_element("css selector", "[data-dialog]").click()
            adam.find_element("css selector", "dialog[open] button.danger").click()
            wait(adam, lambda d: d.find_elements("css selector", f'form[action="/r/{rid}/restore"]'))
            adam.find_element("css selector", f'form[action="/r/{rid}/restore"] button').click()
            wait(adam, lambda d: len(d.find_elements("css selector", ".receipt-card")) == 1)
            adam.get(f"{BASE}/r/{rid}")
            wait(adam, lambda d: holders(d, first, "Adam"))
            print("cancel removal and Undo removal preserve assignments: OK")
        finally:
            for driver in drivers:
                driver.quit()
            process.terminate()
            process.wait()


if __name__ == "__main__":
    main()
