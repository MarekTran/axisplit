"""Benchmark scoring: polygon geometry, label matching, and a perfect prediction scoring 100%."""

import json

from bench import vlm


def test_quad_iou_and_centre():
    square = [[0, 0], [1, 0], [1, 1], [0, 1]]
    assert vlm.quad_iou([0, 0, 1, 1], square) == 1
    assert vlm.quad_iou([2, 2, 3, 3], square) == 0
    assert abs(vlm.quad_iou([0, 0, 0.5, 1], square) - 0.5) < 1e-9
    tilted = [[0, 0], [1, 0.2], [1, 0.3], [0, 0.1]]  # a line sloping down to the right
    assert vlm.centre_in([0, 0, 1, 0.3], tilted)
    assert not vlm.centre_in([0, 0.2, 0.2, 0.3], tilted)


def test_labels_match_without_quantity_or_accents():
    assert vlm.label_sim("1 x bun Heo", "bun Heo") == 1
    assert vlm.label_sim("Borovicka Sp.", "Borovička Sp.") == 1
    assert vlm.label_sim("Homemade Watermelon Tea", "Watermelon Tea") >= 0.9
    assert vlm.label_sim("Nachos", "Coffee") < 0.6


def test_ground_truth_as_prediction_scores_perfectly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(vlm, "PREDS", tmp_path)
    (tmp_path / "oracle").mkdir()
    for case in vlm.cases():
        gt = json.loads((case / "gt.json").read_text())
        items = [{k: i[k] for k in ("label", "price_cents", "bbox")} for i in gt["items"]]
        mode = {"seconds": 1, "error": None, "items": items}
        (tmp_path / "oracle" / f"{case.name}.json").write_text(json.dumps(
            {"whole": mode, "focus": mode, "total": {"seconds": 1, "error": None, "total_cents": gt["total"]["price_cents"]}}))
    vlm.score(type("A", (), {"json": True})())
    whole = json.loads(capsys.readouterr().out)[0]
    assert whole["items_found"] == whole["price_right"] == "73/73"
    assert whole["sum_exact"] == whole["total_right"] == "8/8"
    assert whole["extra_items"] == 0
    assert int(whole["box_on_line"].split("/")[0]) >= 70  # a rectangle's centre can miss a steep quad
