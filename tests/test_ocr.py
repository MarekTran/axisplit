import base64
import io
import json

import httpx
import pytest
from PIL import Image

from axisplit import ocr, receipt_rules
from axisplit.ocr import HttpReader, LocalVisionBackend, PaddleBackend


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


def test_currency_from_signs_else_the_shops_vat_id():
    line = lambda text: (text, box(0, 0, 10, 10))
    assert receipt_rules.currency([line("Celkem"), line("1 035 Kč"), line("190 Kc")]) == "CZK"
    assert receipt_rules.currency([line("TOTAL"), line("€ 74,50")]) == "EUR"
    assert receipt_rules.currency([line("ICO 04973771 DIč CZ04973771"), line("610,00")]) == "CZK"
    assert receipt_rules.currency([line("IC DPH: SK7020000317"), line("121,16")]) == "EUR"
    assert receipt_rules.currency([line("TOTAL 12,40")]) is None


def test_rules_read_items_with_boxes():
    parsed = receipt_rules.parse(RECEIPT, 1000, 2000)
    assert [(i["label"], i["price_cents"]) for i in parsed["items"]] == [("Beer", 450), ("Nachos", 890)]
    assert parsed["items"][0]["bbox"] == [0.1, 0.205, 0.89, 0.22]
    assert parsed["discounts"][0]["price_cents"] == -100
    assert parsed["total_cents"] == 1240


def test_rules_read_quantity_first_names_with_the_price_on_the_row_below():
    # Czech till layout: "5ks 21 % Pilsner" with its line total a little lower, then the unit price;
    # the last item has its price mid-line, left of the price column.
    lines = [
        ("5ks 21 % Pilsner Urquell 0,5", box(100, 400, 600, 430)), ("360,00 Kč", box(700, 440, 900, 470)),
        ("5 ks x 72,00 Kč/ks", box(120, 500, 500, 525)),
        ("4ks 21 % Gin Tonic", box(100, 560, 500, 590)), ("560,00 Kč", box(700, 600, 900, 630)),
        ("4 ks x 140,00 Kč/ks", box(120, 660, 500, 685)),
        ("1ks 21 % Premium 450,00 Kč", box(100, 720, 650, 750)),
        ("1 ks x 450,00 Kč/ks", box(120, 780, 500, 805)),
    ]
    parsed = receipt_rules.parse(lines, 1000, 2000)
    assert [(i["label"], i["price_cents"]) for i in parsed["items"]] == [
        ("Pilsner Urquell 0,5", 36000), ("Gin Tonic", 56000), ("Premium", 45000)]


class FakeOCR:
    """Stands in for RapidOCR: the lines that fit in the crop it is handed, in crop pixels."""
    def __init__(self, lines):
        self.lines, self.origin, self.crops = lines, (0, 0), []

    def __call__(self, bgr):
        self.crops.append(bgr.shape)
        (h, w) = bgr.shape[:2]
        ox, oy = (0, 0) if (h, w) == (2000, 1000) else self.origin  # a pass over the whole fixture photo
        inside = [(t, [[x - ox, y - oy] for x, y in q]) for t, q in self.lines]
        inside = [(t, q) for t, q in inside if all(0 <= x <= w and 0 <= y <= h for x, y in q)]
        return type("Result", (), {"txts": [t for t, _ in inside], "boxes": [q for _, q in inside] or None})()


@pytest.fixture
def paddle(tmp_path, monkeypatch):
    image = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 2000), "white").save(image)
    backend, fake = PaddleBackend(), FakeOCR(RECEIPT)
    monkeypatch.setattr(backend, "_ocr", lambda: fake)
    monkeypatch.setattr(httpx, "post", refuse)  # no decision model unless a test answers
    return backend, fake, image


def refuse(url, **kwargs):
    raise httpx.ConnectError("connection refused", request=httpx.Request("POST", url))


def answer(json=None, text=None):
    return lambda url, **kwargs: httpx.Response(200, request=httpx.Request("POST", url), json=json, text=text)


def test_paddle_reads_the_focus_crop_and_maps_boxes_to_the_photo(paddle):
    backend, fake, image = paddle
    fake.origin = (50, 400)
    beer, nachos = backend.read(image, ocr.corners((0.05, 0.2, 0.9, 0.08))).items  # the two item lines
    assert fake.crops == [(2000, 1000, 3), (160, 900, 3)]  # the whole photo (for the total), then the area
    assert (beer.label, beer.price_cents, nachos.label, nachos.price_cents) == ("Beer", 450, "Nachos", 890)
    assert (beer.x, beer.y, round(beer.w, 4), round(beer.h, 4)) == (0.1, 0.205, 0.79, 0.015)


def test_paddle_leaves_out_lines_outside_a_tilted_area(paddle):
    backend, fake, image = paddle
    fake.origin = (50, 400)
    # A line below a tilted last item: inside the crop (the area's bounds), outside the area.
    fake.lines = [RECEIPT[0], RECEIPT[1], ("Tip 2,00", box(100, 460, 280, 490))]
    quad = [(0.05, 0.2), (0.95, 0.2), (0.95, 0.25), (0.05, 0.226)]
    beer, = backend.read(image, quad).items
    assert (beer.label, beer.price_cents) == ("Beer", 450)
    assert fake.crops[-1] == (100, 900, 3)  # the crop holds that line


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
    assert backend.read(image).total_cents == 1240
    assert sent["url"] == "http://127.0.0.1:8082/v1/systemone"
    question = sent["body"]["questions"]["total"]
    assert question["type"] == "choice" and question["instructions"] == ocr.DECIDE_TOTAL
    assert list(question["criteria"].values()) == [  # every positive amount is a candidate
        "Beer 0,5l 4,50", "Nachos 8,90", "SUBTOTAL 13,40", "TOTAL 12,40", "VAT 21% 2,15"]
    assert "A) Beer 0,5l 4,50" in sent["body"]["state"]["receipt_lines"]
    assert "   Discount -1,00" in sent["body"]["state"]["receipt_lines"]


@pytest.mark.parametrize("respond", [
    refuse,  # down
    answer(json={"answers": {"total": {"choice": "Z"}}}),  # not one of the candidates
    answer(json={"answers": {}}),
    answer(text="<html>Bad gateway</html>"),
], ids=["down", "unknown-letter", "no-answer", "not-json"])
def test_rules_give_the_total_without_a_usable_decision(paddle, monkeypatch, caplog, respond):
    backend, _, image = paddle
    monkeypatch.setattr(httpx, "post", respond)
    assert backend.read(image).total_cents == 1240
    assert "taking the total from the rules" in caplog.text


def test_whole_photo_is_read_once_without_an_area(paddle, monkeypatch):
    backend, fake, image = paddle
    monkeypatch.setattr(httpx, "post", answer(json={"answers": {"total": {"choice": "D"}}}))
    reading = backend.read(image)
    assert (len(reading.items), reading.total_cents) == (2, 1240)
    assert [round(v, 6) for v in reading.extent] == [0.1, 0.205, 0.79, 0.19]  # the text's extent, to frame it
    assert fake.crops == [(2000, 1000, 3)]
    assert backend.locate(image) == reading.extent


def test_items_survive_their_optional_parts_failing(paddle, monkeypatch):
    backend, fake, image = paddle
    fake.origin = (50, 400)
    area = ocr.corners((0.05, 0.2, 0.9, 0.08))
    monkeypatch.setattr(backend, "_total", lambda *a: 1 / 0)
    reading = backend.read(image, area)
    assert len(reading.items) == 2 and reading.total_cents is None and reading.extent

    whole_photo = backend._read
    monkeypatch.setattr(backend, "_read", lambda image, focus: whole_photo(image, focus) if focus else 1 / 0)
    reading = backend.read(image, area)  # the items come from the area's own pass
    assert len(reading.items) == 2 and (reading.total_cents, reading.currency, reading.extent) == (None, None, None)
    with pytest.raises(ZeroDivisionError):  # without an area the items needed that pass: the read fails
        backend.read(image)


def test_backend_choice(monkeypatch):
    monkeypatch.delenv("AXISPLIT_OCR_BACKEND", raising=False)
    assert isinstance(ocr.get_backend(), PaddleBackend)
    monkeypatch.setenv("AXISPLIT_OCR_BACKEND", "vision")
    assert isinstance(ocr.get_backend(), LocalVisionBackend)
    monkeypatch.setenv("AXISPLIT_OCR_BACKEND", "http")
    assert isinstance(ocr.get_backend(), HttpReader)
    monkeypatch.setenv("AXISPLIT_OCR_BACKEND", "qwen")
    with pytest.raises(ValueError):
        ocr.get_backend()


@pytest.fixture
def reader(tmp_path, monkeypatch):
    """The http reader against a fake service; `reader.reply` is its answer."""
    image = tmp_path / "receipt.jpg"
    Image.new("RGB", (600, 1000), "white").save(image)
    monkeypatch.setenv("AXISPLIT_READER_URL", "http://reader:9000/")
    sent = []

    def post(url, **kwargs):
        sent.append({"url": url, **kwargs, "image": kwargs["files"]["image"][1].read()})
        return httpx.Response(200, request=httpx.Request("POST", url), json=reader.reply)

    monkeypatch.setattr(httpx, "post", post)
    reader = type("Reader", (), {"image": image, "sent": sent, "reply": None})
    return reader


def test_http_reader_sends_the_photo_and_area(reader, monkeypatch):
    monkeypatch.setenv("AXISPLIT_READER_KEY", "s3cret")
    monkeypatch.setenv("AXISPLIT_READER_TIMEOUT", "20")
    reader.reply = {
        "items": [
            {"label": " Beer ", "price_cents": 450, "box": [0.1, 0.2, 0.5, 0.02]},
            {"label": "Nachos", "price_cents": 890, "box": [0, 0, 0.1, 0.1],  # the quad wins
             "quad": [[0.1, 0.25], [0.6, 0.24], [0.6, 0.26], [0.1, 0.27]]},
        ],
        "total_cents": 1340, "currency": "CZK", "extent": [0.05, 0.1, 0.9, 0.8], "version_note": "ignored",
    }
    area = ocr.corners((0.05, 0.2, 0.9, 0.1))
    reading = HttpReader().read(reader.image, area)
    beer, nachos = reading.items
    assert (beer.label, beer.price_cents, beer.x, beer.w, beer.quad) == ("Beer", 450, 0.1, 0.5, None)
    assert [round(v, 6) for v in (nachos.x, nachos.y, nachos.w, nachos.h)] == [0.1, 0.24, 0.5, 0.03]
    assert (reading.total_cents, reading.currency, reading.extent) == (1340, "CZK", (0.05, 0.1, 0.9, 0.8))
    sent, = reader.sent
    assert sent["url"] == "http://reader:9000/v1/read"
    assert sent["image"] == reader.image.read_bytes() and sent["files"]["image"][2] == "image/jpeg"
    assert json.loads(sent["data"]["area"]) == [list(p) for p in area]
    assert sent["headers"] == {"Authorization": "Bearer s3cret"} and sent["timeout"].read == 20


def test_http_reader_minimal_reply_and_locate(reader):
    reader.reply = {"items": []}
    reading = HttpReader().read(reader.image)  # no area: no area field, no key: no header
    assert reading == ocr.Reading([]) and reader.sent[0]["data"] is None and reader.sent[0]["headers"] is None
    reader.reply = {"extent": [0.1, 0.1, 0.8, 0.8]}
    assert HttpReader().locate(reader.image) == (0.1, 0.1, 0.8, 0.8)
    assert reader.sent[-1]["url"] == "http://reader:9000/v1/locate"
    reader.reply = {"extent": None}
    assert HttpReader().locate(reader.image) is None


@pytest.mark.parametrize("bad", [
    {"items": [{"label": "Beer", "price_cents": -450, "box": [0.1, 0.2, 0.5, 0.02]}]},
    {"items": [{"label": "Beer", "price_cents": 4.5, "box": [0.1, 0.2, 0.5, 0.02]}]},
    {"items": [{"label": "Beer", "price_cents": "450", "box": [0.1, 0.2, 0.5, 0.02]}]},
    {"items": [{"label": "  ", "price_cents": 450, "box": [0.1, 0.2, 0.5, 0.02]}]},
    {"items": [{"label": "Beer", "price_cents": 450}]},  # no geometry
    {"items": [{"label": "Beer", "price_cents": 450, "box": [0.6, 0.2, 0.5, 0.02]}]},  # past the right edge
    {"items": [{"label": "Beer", "price_cents": 450, "box": [0.1, 0.2, 0, 0.02]}]},  # no width
    {"items": [{"label": "Beer", "price_cents": 450, "box": [0.1, 0.2, 0.5]}]},
    {"items": [{"label": "Beer", "price_cents": 450, "quad": [[0.1, 0.2], [0.5, 0.2], [1.5, 0.3]]}]},
    {"items": [], "total_cents": 12.4},
    {"items": [], "currency": "euro"},
    {"items": [], "extent": [0, 0, 2, 1]},
    {"total_cents": 1240},  # no items
], ids=["negative-price", "fractional-cents", "string-price", "blank-label", "no-geometry", "outside",
        "zero-width", "short-box", "bad-quad", "fractional-total", "currency-name", "extent-outside",
        "no-items"])
def test_http_reader_refuses_invalid_replies(reader, bad):
    reader.reply = bad
    with pytest.raises(ValueError):
        HttpReader().read(reader.image)


def test_http_reader_fails_on_errors_and_needs_a_url(reader, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(503, request=httpx.Request("POST", url)))
    with pytest.raises(httpx.HTTPStatusError):
        HttpReader().read(reader.image)
    monkeypatch.delenv("AXISPLIT_READER_URL")
    with pytest.raises(ValueError, match="AXISPLIT_READER_URL"):
        HttpReader().read(reader.image)
