import base64
import io
import json

import httpx
import pytest
from PIL import Image

from axisplit import ocr, receipt_rules
from axisplit.ocr import LocalVisionBackend, PaddleBackend


def test_real_request_and_box_conversion(tmp_path, monkeypatch):
    image = tmp_path / "receipt.jpg"
    Image.new("RGB", (600, 1000)).save(image)

    def respond(url, **kwargs):
        assert url.endswith("/v1/chat/completions")
        body = kwargs["json"]
        assert body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert body["response_format"]["type"] == "json_schema"
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
                "items": [{"label": "Beer x2", "price_cents": 400,
                           "bbox": [100, 600, 900, 650]}],
            })}}],
        })

    monkeypatch.setattr(httpx, "post", respond)
    item, = LocalVisionBackend().extract_items(image)
    assert item.price_cents == 400
    assert (item.x, item.y, item.w, item.h) == (0.1, 0.6, 0.8, 0.05)


@pytest.mark.parametrize("box", [[900, 600, 100, 650], [0, 0, 1001, 100], [0, 0, 0, 0]])
def test_invalid_boxes_are_not_saved(tmp_path, monkeypatch, box):
    image = tmp_path / "receipt.jpg"
    Image.new("RGB", (100, 100)).save(image)
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(
        200, request=httpx.Request("POST", url), json={"choices": [{
            "finish_reason": "stop", "message": {"content": json.dumps({"items": [{
                "label": "Beer", "price_cents": 200, "bbox": box,
            }]})},
        }]},
    ))
    with pytest.raises(ValueError):
        LocalVisionBackend().extract_items(image)


def test_focus_area_is_cropped_upscaled_and_mapped_back(tmp_path, monkeypatch):
    image = tmp_path / "receipt.jpg"
    Image.new("RGB", (600, 1000)).save(image)
    sent = {}

    def respond(url, **kwargs):
        content = kwargs["json"]["messages"][0]["content"]
        sent["prompt"] = content[0]["text"]
        data = base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1])
        sent["size"] = Image.open(io.BytesIO(data)).size
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
                "items": [{"label": "Beer", "price_cents": 400, "bbox": [0, 500, 1000, 600]}],
            })}}],
        })

    monkeypatch.setattr(httpx, "post", respond)
    item, = LocalVisionBackend().extract_items(image, focus=(.1, .2, .5, .4))  # 300 x 400 px
    assert sent["size"] == (1200, 1600)
    assert sent["prompt"].startswith("The user cropped this image")
    assert (round(item.x, 6), round(item.y, 6), round(item.w, 6), round(item.h, 6)) == (.1, .4, .5, .04)


def test_total_is_read_separately(tmp_path, monkeypatch):
    image = tmp_path / "receipt.jpg"
    Image.new("RGB", (600, 1000)).save(image)
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: httpx.Response(
        200, request=httpx.Request("POST", url),
        json={"choices": [{"finish_reason": "stop", "message": {"content": '{"total_cents": 6180}'}}]}))
    assert LocalVisionBackend().extract_total(image) == 6180


def box(x1, y1, x2, y2):
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


# A small receipt as OCR lines in photo pixels (1000 x 2000): two items, discounts, VAT, total.
RECEIPT = [
    ("Beer 0,5l", box(100, 410, 400, 440)), ("4,50", box(800, 410, 890, 440)),
    ("Nachos", box(100, 460, 300, 490)), ("8,90", box(800, 460, 890, 490)),
    ("SUBTOTAL", box(100, 560, 350, 590)), ("13,40", box(780, 560, 890, 590)),
    ("Discount", box(100, 610, 330, 640)), ("-1,00", box(800, 610, 890, 640)),
    ("TOTAL", box(100, 660, 280, 690)), ("12,40", box(780, 660, 890, 690)),
    ("VAT 21% 2,15", box(100, 760, 890, 790)),
]


def test_rules_read_items_with_boxes():
    parsed = receipt_rules.parse(RECEIPT, 1000, 2000)
    assert [(i["label"], i["price_cents"]) for i in parsed["items"]] == [("Beer", 450), ("Nachos", 890)]
    assert parsed["items"][0]["bbox"] == [0.1, 0.205, 0.89, 0.22]
    assert parsed["discounts"][0]["price_cents"] == -100
    assert parsed["total_cents"] == 1240


class FakeOCR:
    """Stands in for RapidOCR: the lines that fit in the crop it is handed, in crop pixels."""
    def __init__(self, lines):
        self.lines, self.origin, self.crops = lines, (0, 0), []

    def __call__(self, bgr):
        self.crops.append(bgr.shape)
        (ox, oy), (h, w) = self.origin, bgr.shape[:2]
        inside = [(t, [[x - ox, y - oy] for x, y in q]) for t, q in self.lines]
        inside = [(t, q) for t, q in inside if all(0 <= x <= w and 0 <= y <= h for x, y in q)]
        return type("Result", (), {"txts": [t for t, _ in inside], "boxes": [q for _, q in inside] or None})()


@pytest.fixture
def paddle(tmp_path, monkeypatch):
    image = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 2000), "white").save(image)
    backend, fake = PaddleBackend(), FakeOCR(RECEIPT)
    monkeypatch.setattr(backend, "_ocr", lambda: fake)
    return backend, fake, image


def test_paddle_reads_the_focus_crop_and_maps_boxes_to_the_photo(paddle):
    backend, fake, image = paddle
    fake.origin = (50, 400)
    beer, nachos = backend.extract_items(image, focus=(0.05, 0.2, 0.9, 0.08))  # the two item lines
    assert fake.crops == [(160, 900, 3)]
    assert (beer.label, beer.price_cents, nachos.label, nachos.price_cents) == ("Beer", 450, "Nachos", 890)
    assert (beer.x, beer.y, round(beer.w, 4), round(beer.h, 4)) == (0.1, 0.205, 0.79, 0.015)


def test_paddle_leaves_out_lines_outside_a_tilted_area(paddle):
    backend, fake, image = paddle
    fake.origin = (50, 400)
    # The crop still holds both rows; the area's bottom edge runs between them.
    quad = [(0.05, 0.2), (0.95, 0.2), (0.95, 0.226), (0.05, 0.222)]
    beer, = backend.extract_items(image, focus=(0.05, 0.2, 0.9, 0.08), quad=quad)
    assert (beer.label, beer.price_cents) == ("Beer", 450)


def test_decision_model_picks_the_total_from_the_amount_lines(paddle, monkeypatch):
    backend, _, image = paddle
    sent = {}

    def respond(url, **kwargs):
        sent["url"], sent["body"] = url, kwargs["json"]
        total = sent["body"]["questions"]["total"]
        letter = next(k for k, line in total["criteria"].items() if line.startswith("TOTAL"))
        return httpx.Response(200, request=httpx.Request("POST", url),
                              json={"answers": {"total": {"choice": letter, "confidence": 0.9}}})

    monkeypatch.setattr(httpx, "post", respond)
    assert backend.extract_total(image) == 1240
    assert sent["url"] == "http://127.0.0.1:8082/v1/systemone"
    question = sent["body"]["questions"]["total"]
    assert question["type"] == "choice" and question["instructions"] == ocr.DECIDE_TOTAL
    assert list(question["criteria"].values()) == [  # every positive amount is a candidate
        "Beer 0,5l 4,50", "Nachos 8,90", "SUBTOTAL 13,40", "TOTAL 12,40", "VAT 21% 2,15"]
    assert "A) Beer 0,5l 4,50" in sent["body"]["state"]["receipt_lines"]
    assert "   Discount -1,00" in sent["body"]["state"]["receipt_lines"]


def test_rules_give_the_total_when_the_decision_model_is_down(paddle, monkeypatch):
    backend, _, image = paddle

    def refuse(url, **kwargs):
        raise httpx.ConnectError("connection refused", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", refuse)
    assert backend.extract_total(image) == 1240


def test_whole_photo_is_read_once_for_items_and_total(paddle, monkeypatch):
    backend, fake, image = paddle
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(
        200, request=httpx.Request("POST", url), json={"answers": {"total": {"choice": "D"}}}))
    assert len(backend.extract_items(image)) == 2
    assert backend.extract_total(image) == 1240
    assert fake.crops == [(2000, 1000, 3)]


def test_backend_choice(monkeypatch):
    monkeypatch.delenv("AXISPLIT_OCR_BACKEND", raising=False)
    assert isinstance(ocr.get_backend(), PaddleBackend)
    monkeypatch.setenv("AXISPLIT_OCR_BACKEND", "vision")
    assert isinstance(ocr.get_backend(), LocalVisionBackend)
    monkeypatch.setenv("AXISPLIT_OCR_BACKEND", "qwen")
    with pytest.raises(ValueError):
        ocr.get_backend()
