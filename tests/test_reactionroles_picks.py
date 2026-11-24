"""What a click or a pick in a role menu changes."""

from features.reactionroles.picks import button_plan, select_plan

MENU = ["1", "2", "3"]


def test_a_click_gives_the_role_or_takes_it_back():
    assert button_plan(MENU, set(), "2", None).add == ["2"]
    assert button_plan(MENU, {"2"}, "2", None).remove == ["2"]


def test_one_role_at_a_time_replaces_the_old_one():
    plan = button_plan(MENU, {"1", "9"}, "3", 1)
    assert (plan.add, plan.remove) == (["3"], ["1"])  # a role outside the menu stays


def test_a_member_at_the_limit_is_told_to_remove_one():
    plan = button_plan(MENU, {"1", "2"}, "3", 2)
    assert plan.add == [] and "retires-en un" in plan.refusal
    assert button_plan(MENU, {"1"}, "3", 2).add == ["3"]


def test_a_pick_leaves_exactly_the_roles_picked():
    plan = select_plan(MENU, {"1", "2", "9"}, ["2", "3", "7"])
    assert (plan.add, plan.remove) == (["3"], ["1"])
    assert select_plan(MENU, {"1"}, []).remove == ["1"]
