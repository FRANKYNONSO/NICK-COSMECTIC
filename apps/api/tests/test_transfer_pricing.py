from api.v1.transfer_pricing import branch_selling_price_kobo, default_transfer_price


def test_selling_price_when_no_wholesale_tier():
    assert default_transfer_price(100500, None, None, 42) == (100500, "selling")


def test_selling_price_below_wholesale_minimum():
    assert default_transfer_price(100500, 100400, 10, 9) == (100500, "selling")


def test_wholesale_price_at_minimum():
    assert default_transfer_price(100500, 100400, 10, 10) == (100400, "wholesale")


def test_wholesale_price_above_minimum():
    assert default_transfer_price(100500, 100400, 10, 42) == (100400, "wholesale")


def test_wholesale_needs_both_price_and_minimum():
    assert default_transfer_price(100500, 100400, None, 50) == (100500, "selling")
    assert default_transfer_price(100500, None, 10, 50) == (100500, "selling")
