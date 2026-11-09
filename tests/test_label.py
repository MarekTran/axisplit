"""The labelling tool's checks and the ground truth it writes (bench/label/labels.py)."""

from bench.label import labels

PRE = {"size": [1000, 2000], "lines": [{"text": t, "quad": [[0.1, y], [0.9, y], [0.9, y + 0.01], [0.1, y + 0.01]]}
                                       for t, y in (("Burger 12,00", 0.1), ("+ Cheddar 1,50", 0.12), ("2 ks * 2,40", 0.14),
                                                    ("Kofola 4,80", 0.16), ("Zľava -1,00", 0.18), ("SUMA 17,30", 0.2))]}


def entry(kind, label, price, lines, **kw):
    return {"kind": kind, "label": label, "price_cents": price, "lines": lines, "extra_quads": [], "quantity": None,
            "unit_cents": None, "parent": None, "flag": None, **kw}


def label(**changes):
    base = {"id": "r1", "status": "agent", "currency": "EUR",
            "receipt": {"type": "bar", "qty_format": "line", "unit_price_printed": True},
            "items": [entry("item", "Burger", 1200, [0]), entry("modifier", "+ Cheddar", 150, [1], parent=0),
                      entry("item", "Kofola", 480, [2, 3], quantity=2, unit_cents=240), entry("discount", "Zľava", -100, [4], parent=2)],
            "total": {"label": "SUMA", "price_cents": 1730, "lines": [5], "extra_quads": [], "flag": None}, "note": ""}
    base.update(changes)
    return base


def test_a_consistent_label_has_no_problems():
    assert labels.check(label(), PRE) == []


def test_quantity_times_unit_price_must_be_the_line_total():
    bad = label()
    bad["items"][2]["unit_cents"] = 250
    assert labels.check(bad, PRE) == ["Entry 3: 2 × 2,50 = 5,00, not 4,80."]


def test_modifiers_need_their_item_and_flags_block_review():
    bad = label()
    bad["items"][1]["parent"] = None
    bad["items"][3]["flag"] = "for the whole receipt?"
    problems = labels.check(bad, PRE)
    assert "Entry 2: which item is this modifier for?" in problems
    assert "Entry 4: flagged: for the whole receipt?" in problems


def test_lines_belong_to_one_entry_and_must_add_up():
    bad = label()
    bad["items"][0]["lines"] = [0, 1]
    bad["total"]["price_cents"] = 1800
    problems = labels.check(bad, PRE)
    assert "Entry 2: line 1 is also in entry 1." in problems
    assert any(p.startswith("Lines add up to 17,30, the total is 18,00") for p in problems)
    bad["note"] = "a line is cut off at the photo's edge"
    assert not any(p.startswith("Lines add up") for p in labels.check(bad, PRE))


def test_ground_truth_keeps_the_new_fields_and_points_parents_at_gt_items():
    gt = labels.to_gt(label(), PRE, "../../images/r1.jpg")
    burger, cheddar, kofola = gt["items"]
    assert (cheddar["kind"], cheddar["parent"], kofola["quantity"], kofola["unit_cents"]) == ("modifier", 0, 2, 240)
    assert "kind" not in burger and "quantity" not in burger
    assert gt["discounts"] == [{**gt["discounts"][0], "price_cents": -100, "parent": 2}]
    assert sum(i["price_cents"] for i in gt["items"] + gt["discounts"]) == gt["total"]["price_cents"]
    assert gt["receipt"] == {"type": "bar", "qty_format": "line", "unit_price_printed": True}
