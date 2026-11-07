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
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8782"


def open_browser():
    options = Options()
    options.add_argument("--headless")
    driver = webdriver.Firefox(service=Service(), options=options)
    driver.set_window_size(1100, 1000)
    return driver


def wait(driver, condition):
    WebDriverWait(driver, 8).until(condition)


def holders(driver, item, user):
    return driver.find_elements("css selector", f'.holder[data-item="{item}"][data-user="{user}"]')


def tick(driver, user):
    return driver.find_element("css selector", f'.tick[data-user="{user}"]')


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
            image = io.BytesIO()
            Image.new("RGB", (400, 600), "#f1eee5").save(image, "JPEG")
            page = client.post("/receipts", files={"image": ("receipt.jpg", image.getvalue(), "image/jpeg")})
            rid = page.url.path.rsplit("/", 1)[-1]
            client.post(f"/r/{rid}/people", data={"username": "Adam"})
            client.post(f"/r/{rid}/items", data={"label": "Mango tea", "price": "10", "x": ".5", "y": ".4"})
            client.post(f"/r/{rid}/items", data={"label": "Watermelon tea", "price": "20", "x": ".5", "y": ".6"})
            adam = open_browser(); drivers.append(adam)  # the organiser
            peer = open_browser(); drivers.append(peer)  # a friend with the same link: no name to claim
            for driver in drivers:
                driver.get(f"{BASE}/r/{rid}")
            wait(adam, lambda d: d.find_elements("css selector", ".item-add"))
            item_ids = [i.get_attribute("data-item") for i in adam.find_elements("css selector", ".item-review li")]
            first, second = item_ids
            # Nina is added from the bar; she shows up in the friend's bar too, and is picked to tap for.
            adam.find_element("css selector", "[data-add-person]").click()
            adam.find_element("css selector", "#add-person input").send_keys("Nina", Keys.ENTER)
            wait(peer, lambda d: d.find_elements("css selector", '#tokenbar .token[data-user="Nina"]'))
            wait(adam, lambda d: d.find_element("css selector", '#tokenbar .token[data-user="Nina"]').get_attribute("aria-pressed") == "true")
            place(adam, first, "Adam")
            wait(adam, lambda d: holders(d, first, "Adam"))
            place(peer, second, "Nina")
            wait(adam, lambda d: holders(d, second, "Nina"))
            tick(adam, "Adam").click()
            wait(peer, lambda d: tick(d, "Adam").get_attribute("aria-pressed") == "true")
            tick(peer, "Nina").click()
            wait(adam, lambda d: d.find_element("id", "status").text == "Everyone agreed")
            print("people added live, assignment from both browsers, anyone ticks anyone off: OK")

            holders(adam, first, "Adam")[0].click()
            wait(adam, lambda d: not holders(d, first, "Adam"))
            wait(adam, lambda d: d.find_element("id", "undo-token").is_displayed())
            adam.find_element("id", "undo-token").click()
            wait(adam, lambda d: holders(d, first, "Adam"))
            wait(adam, lambda d: tick(d, "Adam").get_attribute("aria-pressed") == "true")  # same share again
            print("token Undo restores assignment and Adam's tick: OK")

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

            # Taking someone off who is on items asks first, and their items go to the others.
            adam.find_element("css selector", '[data-remove-person="Nina"]').click()
            wait(adam, lambda d: d.find_element("id", "person-dialog").is_displayed())
            adam.find_element("css selector", "#person-dialog [data-close-dialog]").click()
            assert holders(adam, first, "Nina")
            adam.find_element("css selector", '[data-remove-person="Nina"]').click()
            adam.find_element("css selector", "#person-dialog [data-remove-confirm]").click()
            wait(peer, lambda d: not d.find_elements("css selector", '#tokenbar .token[data-user="Nina"]'))
            wait(adam, lambda d: "1/2 assigned" in d.find_element("id", "review").text)
            print("removing a person asks first, then drops their tokens on every phone: OK")

            # The focus step after choosing a photo, and the home page listing this device's splits.
            photo = Path(data) / "second.jpg"
            Image.new("RGB", (400, 600), "#e8e4da").save(photo, "JPEG")
            adam.get(BASE)
            adam.find_element("id", "receipt-image").send_keys(str(photo))
            wait(adam, lambda d: d.find_element("id", "focus-dialog").is_displayed())
            adam.find_element("css selector", '[data-focus="whole"]').click()
            wait(adam, lambda d: "/r/" in d.current_url and d.find_elements("id", "add-person"))
            adam.get(BASE)
            assert len(adam.find_elements("css selector", ".receipt-card")) == 2
            print("upload from home, and home lists this device's splits: OK")
        finally:
            for driver in drivers:
                driver.quit()
            process.terminate()
            process.wait()


if __name__ == "__main__":
    main()
