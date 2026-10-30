from api.v1.transfer_pricing import plan_transfer_split, transfer_split_text


def test_shelf_choice_enough_on_shelf():
    plan = plan_transfer_split(10, "shelf", 14, 376, 0, None)
    assert plan["from_shelf"] == 10 and plan["from_warehouse"] == 0


def test_shelf_choice_shortfall_comes_from_warehouse():
    plan = plan_transfer_split(40, "shelf", 14, 376, 0, None)
    assert plan["from_shelf"] == 14 and plan["from_warehouse"] == 26


def test_warehouse_choice_enough_in_warehouse():
    plan = plan_transfer_split(40, "warehouse", 14, 376, 0, None)
    assert plan["from_warehouse"] == 40 and plan["from_shelf"] == 0


def test_warehouse_choice_shortfall_comes_from_shelf():
    plan = plan_transfer_split(390, "warehouse", 14, 376, 0, None)
    assert plan["from_warehouse"] == 376 and plan["from_shelf"] == 14


def test_above_both_together_is_refused():
    assert plan_transfer_split(391, "shelf", 14, 376, 0, None) is None
    assert plan_transfer_split(390, "shelf", 14, 376, 0, None) is not None


def test_zero_or_negative_quantity_is_refused():
    assert plan_transfer_split(0, "shelf", 14, 376, 0, None) is None


def test_landing_fills_shelf_up_to_default_rest_to_warehouse():
    plan = plan_transfer_split(40, "shelf", 14, 376, 0, 24)
    assert plan["to_shelf"] == 24 and plan["to_warehouse"] == 16


def test_landing_counts_room_left_on_a_partly_full_shelf():
    plan = plan_transfer_split(10, "shelf", 14, 376, 20, 24)
    assert plan["to_shelf"] == 4 and plan["to_warehouse"] == 6


def test_landing_shelf_already_full_all_to_warehouse():
    plan = plan_transfer_split(10, "shelf", 14, 376, 30, 24)
    assert plan["to_shelf"] == 0 and plan["to_warehouse"] == 10


def test_landing_without_saved_default_everything_to_shelf():
    plan = plan_transfer_split(40, "shelf", 14, 376, 5, None)
    assert plan["to_shelf"] == 40 and plan["to_warehouse"] == 0


def test_both_takes_the_warehouse_first_and_the_shelf_covers_the_rest():
    plan = plan_transfer_split(380, "both", 14, 376, 0, None)
    assert plan["from_warehouse"] == 376 and plan["from_shelf"] == 4


def test_split_text():
    plan = plan_transfer_split(40, "shelf", 14, 376, 0, 24)
    assert transfer_split_text(plan) == "Taken from shelf 14 + warehouse 26. Arrives as shelf 24 + warehouse 16."
