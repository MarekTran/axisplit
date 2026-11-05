import base64
import io
import json

import httpx
import pytest
from PIL import Image

from axisplit.ocr import LocalVisionBackend


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
