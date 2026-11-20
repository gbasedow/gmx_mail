"""Tests for option validation."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.gmx_mail.const import (
    CONF_ALLOWLIST,
    CONF_FOLDER_SENT,
    CONF_IDLE_RENEW_MINUTES,
    CONF_MAX_RECIPIENTS,
    CONF_MAX_SEND_PER_DAY,
    CONF_PERMISSIONS,
    CONF_POLL_INTERVAL,
    CONF_SEND_MODE,
    CONF_SENT_COPY_MODE,
    DEFAULT_OPTIONS,
    FolderRole,
)
from custom_components.gmx_mail.options import (
    OPTION_SECTIONS,
    flatten,
    folder_overrides,
    is_valid_allowlist_entry,
    nest,
    normalize_allowlist,
    validate_options,
    with_defaults,
)


def test_defaults_are_valid_and_restrictive() -> None:
    """Default: read only, sending off, no allowlist (SPEC section 5)."""
    options, errors = validate_options({})
    assert errors == {}
    assert options == DEFAULT_OPTIONS
    assert options[CONF_PERMISSIONS] == ["read"]
    assert options[CONF_SEND_MODE] == "off"
    assert options[CONF_ALLOWLIST] == []
    assert options[CONF_SENT_COPY_MODE] == "append"


def test_every_option_has_exactly_one_section() -> None:
    """The form layout covers all options."""
    keys = [key for keys in OPTION_SECTIONS.values() for key in keys]
    assert sorted(keys) == sorted(DEFAULT_OPTIONS)


def test_nest_flatten_roundtrip() -> None:
    """Sections are split and merged without loss."""
    assert flatten(nest(DEFAULT_OPTIONS)) == DEFAULT_OPTIONS
    assert flatten({"unknown_section": {"x": 1}}) == {}


@pytest.mark.parametrize(
    ("entry", "valid"),
    [
        ("anna@example.org", True),
        ("*@example.org", True),
        ("*", True),
        ("anna@localhost", False),
        ("anna", False),
        ("*@*.org", False),
        ("a*b@example.org", False),
        ("@example.org", False),
        ("anna@example.", False),
        ("anna@@example.org", False),
    ],
)
def test_allowlist_entries(entry: str, valid: bool) -> None:
    """Only exact addresses, *@domain and * are accepted."""
    assert is_valid_allowlist_entry(entry) is valid


def test_allowlist_normalisation() -> None:
    """Entries are split, trimmed, lower-cased and de-duplicated."""
    assert normalize_allowlist([" Anna@Example.org ", "anna@example.org, *@Firma.de", ""]) == ["anna@example.org", "*@firma.de"]
    assert normalize_allowlist("x@y.de") == ["x@y.de"]
    assert normalize_allowlist(None) == []


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({CONF_PERMISSIONS: ["organize"]}, "read_required"),
        ({CONF_PERMISSIONS: ["read", "admin"]}, "invalid_permission"),
        ({CONF_SEND_MODE: "everything"}, "invalid_send_mode"),
        ({CONF_SEND_MODE: "allowlist", CONF_ALLOWLIST: ["not-an-address"]}, "invalid_allowlist_entry"),
        ({CONF_SEND_MODE: "allowlist", CONF_ALLOWLIST: ["*"]}, "wildcard_requires_any"),
        ({CONF_SEND_MODE: "off", CONF_ALLOWLIST: ["*"]}, "wildcard_requires_any"),
        ({CONF_SEND_MODE: "any", CONF_ALLOWLIST: []}, "any_requires_wildcard"),
        ({CONF_SEND_MODE: "any", CONF_ALLOWLIST: ["a@b.de"]}, "any_requires_wildcard"),
        ({CONF_SENT_COPY_MODE: "twice"}, "invalid_sent_copy_mode"),
        ({CONF_MAX_RECIPIENTS: 11}, "out_of_range"),
        ({CONF_MAX_SEND_PER_DAY: 0}, "out_of_range"),
        ({CONF_IDLE_RENEW_MINUTES: 26}, "out_of_range"),
        ({CONF_POLL_INTERVAL: "fast"}, "out_of_range"),
    ],
)
def test_invalid_options(changes: dict[str, Any], error: str) -> None:
    """Invalid combinations are rejected with a specific error."""
    _options, errors = validate_options({**DEFAULT_OPTIONS, **changes})
    assert errors == {"base": error}


def test_valid_send_configurations() -> None:
    """Allowlist mode with an empty list is allowed (sending stays blocked); any needs '*'."""
    options, errors = validate_options({CONF_SEND_MODE: "allowlist", CONF_ALLOWLIST: []})
    assert errors == {}
    options, errors = validate_options({CONF_SEND_MODE: "any", CONF_ALLOWLIST: ["*"], CONF_PERMISSIONS: ["send", "read"]})
    assert errors == {}
    assert options[CONF_PERMISSIONS] == ["read", "send"]  # canonical order


def test_numbers_are_coerced_and_folder_overrides_trimmed() -> None:
    """Number selectors deliver floats; folder names are trimmed."""
    options, errors = validate_options({CONF_POLL_INTERVAL: 120.0, CONF_FOLDER_SENT: "  Gesendet "})
    assert errors == {}
    assert options[CONF_POLL_INTERVAL] == 120
    assert isinstance(options[CONF_POLL_INTERVAL], int)
    assert folder_overrides(options) == {FolderRole.SENT: "Gesendet"}


def test_with_defaults_drops_unknown_keys() -> None:
    """Old or foreign keys do not survive."""
    assert "legacy" not in with_defaults({"legacy": True})
