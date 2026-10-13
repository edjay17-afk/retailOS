"""The V1 formula checked against hand calculations (spec acceptance test 1)."""
import pytest

from app.services.replenishment import calculate


def base(**kw):
    d = dict(ads=2.0, trading_days=14, lookback=14, on_hand=5.0, incoming=0.0, lead=3.0, review=1.0, safety_days=2.0,
             case_pack=None, order_multiple=None, moq=None, max_stock=None, max_cover_days=30, min_trading_days=5)
    d.update(kw)
    return calculate(**d)


def test_basic_formula_by_hand():
    # ADS 2, (lead 3 + review 1) = 4 days -> 8, safety 2 days = 4, target 12; position 5; order 7
    r = base()
    assert r["target"] == 12.0 and r["safety_stock"] == 4.0 and r["position"] == 5.0
    assert r["raw"] == 7.0 and r["final"] == 7.0


def test_incoming_reduces_the_order():
    assert base(incoming=4.0)["final"] == 3.0


def test_nothing_to_order_when_covered():
    assert base(on_hand=20.0)["final"] == 0.0


def test_negative_on_hand_is_treated_as_zero():
    r = base(on_hand=-10.0)
    assert r["position"] == 0.0 and r["final"] == 12.0


def test_case_pack_rounds_up():
    r = base(case_pack=6)
    assert r["final"] == 12.0 and r["cases"] == 2.0
    assert any(s["step"] == "case_pack" for s in r["steps"])


def test_order_multiple_then_moq():
    r = base(case_pack=1, order_multiple=5, moq=20)
    assert r["final"] == 20.0


def test_max_stock_cap_never_exceeded():
    r = base(case_pack=6, max_stock=14.0)  # position 5 + order must stay <= 14 -> order 6 (one case)
    assert r["final"] == 6.0 and 5.0 + r["final"] <= 14.0


def test_default_cover_cap_when_no_max_stock():
    r = base(ads=10.0, on_hand=0.0, max_cover_days=2)  # target would be 60, cap is 20
    assert r["final"] == 20.0


def test_zero_sales_means_no_order():
    r = base(ads=0.0)
    assert r["final"] == 0.0 and r["days_cover"] is None


def test_days_of_cover():
    assert base(on_hand=5.0)["days_cover"] == 2.5


def test_short_history_flag():
    assert base(trading_days=3)["history_short"] is True
    assert base(trading_days=14)["history_short"] is False


def test_sales_only_ignores_stock_on_hand():
    # ADS 2 x 7 cover days = 14 whatever is on the shelf; stock figures (even negative or huge) change nothing
    for on_hand in (-10.0, 0.0, 5.0, 500.0):
        r = base(on_hand=on_hand, use_stock=False, cover_days=7.0)
        assert r["target"] == 14.0 and r["position"] == 0.0 and r["final"] == 14.0 and r["days_cover"] is None
    # what is already on order is still subtracted, so the same need is not ordered twice
    assert base(use_stock=False, cover_days=7.0, incoming=6.0)["final"] == 8.0
    assert base(use_stock=False, cover_days=7.0, incoming=20.0)["final"] == 0.0
    # case packs and the maximum-cover cap still apply
    assert base(use_stock=False, cover_days=7.0, case_pack=6.0)["final"] == 18.0
    assert base(use_stock=False, cover_days=40.0, max_cover_days=30)["final"] == 60.0
