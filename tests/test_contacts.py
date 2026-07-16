"""Address-book aliases resolve to addresses, or fail loudly."""

import pytest

from mailrun.contacts import resolve_recipients
from mailrun.errors import ContactCycleError, UnknownContactError

TEAM_ADDRESS_BOOK = {
    "lead":     ("lead@example.com",),
    "analyst":  ("analyst@example.com",),
    "reviewer": ("reviewer@example.net",),
    "team":     ("lead", "analyst", "reviewer"),
    "everyone": ("team", "observer@example.org"),
}


def test_plain_address_passes_through_untouched():
    assert resolve_recipients("lead@example.com", address_book={}) == [
        "lead@example.com"
    ]


def test_lone_string_is_one_recipient_not_one_per_character():
    actual = resolve_recipients("lead@example.com", address_book=TEAM_ADDRESS_BOOK)
    assert actual == ["lead@example.com"]


def test_alias_expands_to_its_address():
    actual = resolve_recipients("lead", address_book=TEAM_ADDRESS_BOOK)
    assert actual == ["lead@example.com"]


def test_group_alias_expands_through_member_aliases():
    actual = resolve_recipients("team", address_book=TEAM_ADDRESS_BOOK)
    assert actual == [
        "lead@example.com",
        "analyst@example.com",
        "reviewer@example.net",
    ]


def test_group_may_nest_groups_and_bare_addresses():
    actual = resolve_recipients("everyone", address_book=TEAM_ADDRESS_BOOK)
    assert actual == [
        "lead@example.com",
        "analyst@example.com",
        "reviewer@example.net",
        "observer@example.org",
    ]


def test_addresses_and_aliases_may_be_mixed():
    actual = resolve_recipients(
        ["lead", "outsider@example.org"], address_book=TEAM_ADDRESS_BOOK
    )
    assert actual == ["lead@example.com", "outsider@example.org"]


def test_address_reached_twice_is_delivered_once():
    actual = resolve_recipients(
        ["team", "lead", "lead@example.com"], address_book=TEAM_ADDRESS_BOOK
    )
    assert actual == [
        "lead@example.com",
        "analyst@example.com",
        "reviewer@example.net",
    ]


def test_first_seen_order_is_preserved():
    actual = resolve_recipients(
        ["reviewer", "lead"], address_book=TEAM_ADDRESS_BOOK
    )
    assert actual == ["reviewer@example.net", "lead@example.com"]


def test_empty_recipients_resolve_to_nothing():
    assert resolve_recipients((), address_book=TEAM_ADDRESS_BOOK) == []


def test_unknown_alias_names_itself_and_the_known_aliases():
    with pytest.raises(UnknownContactError) as caught:
        resolve_recipients("nobody", address_book=TEAM_ADDRESS_BOOK)
    message = str(caught.value)
    assert "nobody" in message
    assert "lead" in message


def test_alias_loop_is_reported_rather_than_recursing_forever():
    looping_address_book = {"here": ("there",), "there": ("here",)}
    with pytest.raises(ContactCycleError) as caught:
        resolve_recipients("here", address_book=looping_address_book)
    assert "here -> there -> here" in str(caught.value)


def test_alias_pointing_at_itself_is_a_loop():
    with pytest.raises(ContactCycleError):
        resolve_recipients("me", address_book={"me": ("me",)})
