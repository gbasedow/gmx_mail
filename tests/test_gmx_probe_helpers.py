"""Tests fuer die reinen Hilfsfunktionen von tools/gmx_probe.py (keine Netzwerkzugriffe)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import gmx_probe  # noqa: E402


def test_utf7_encode_known_values() -> None:
    assert gmx_probe.utf7_encode("Entwürfe") == "Entw&APw-rfe"
    assert gmx_probe.utf7_encode("INBOX") == "INBOX"
    assert gmx_probe.utf7_encode("A&B") == "A&-B"


def test_utf7_roundtrip() -> None:
    for name in ["Entwürfe", "HA-Probe-ÄA", "Rechnungen/2026", "日本語", "A&B", "Größe & Übung"]:
        assert gmx_probe.utf7_decode(gmx_probe.utf7_encode(name)) == name


def test_decode_list() -> None:
    lines = [b'(\\HasNoChildren \\Sent) "/" "Gesendet"', b'(\\HasNoChildren) "/" "Entw&APw-rfe"', None]
    parsed = gmx_probe.decode_list(lines)
    assert parsed[0]["name"] == "Gesendet" and "\\Sent" in parsed[0]["flags"]
    assert parsed[1]["name"] == "Entwürfe" and parsed[1]["delimiter"] == "/"


def test_parse_uid() -> None:
    assert gmx_probe.parse_uid([b"[APPENDUID 1234 56] APPEND completed"], "APPENDUID") == [56]
    assert gmx_probe.parse_uid([b"[COPYUID 1234 7 56] COPY completed"], "COPYUID") == [56]
    assert gmx_probe.parse_uid([b"[COPYUID 1234 7:8 56:57] done"], "COPYUID") == [56, 57]
    assert gmx_probe.parse_uid([b"OK done"], "COPYUID") == []


def test_imap_date_is_locale_independent() -> None:
    assert gmx_probe.imap_date(0) == "01-Jan-1970"
    assert gmx_probe.imap_date(1767225600) == "01-Jan-2026"  # 2026-01-01 00:00 UTC
