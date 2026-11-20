#!/usr/bin/env python3
"""gmx_probe.py - Live-Test gegen ein GMX-Konto (nur Metadaten, keine Mailinhalte).

Aufruf (App-Passwort verwenden, bevorzugt ein Testkonto):
    export GMX_USER='testkonto@gmx.de'
    export GMX_PASS='<App-Passwort>'
    python3 tools/gmx_probe.py > gmx_probe_result.json
    python3 tools/gmx_probe.py --send-self-test > gmx_probe_result_send.json

Sicherheitsregeln dieses Skripts:
  * Es gibt nie Passwort, Adressen, Betreffs oder Mailinhalte aus.
  * Im Standardlauf werden nur zwei eigene Testordner angelegt/benutzt und wieder geloescht.
    Existieren sie schon, bricht das Skript ab.
  * Im Standardlauf wird keine Mail versendet (SMTP: nur EHLO, STARTTLS, Login).
  * --send-self-test sendet genau eine Mail an die eigene Adresse und loescht danach die beiden
    selbst erzeugten Testmails (Posteingang und Gesendet) anhand ihrer eindeutigen Message-ID.
  * Ordnernamen werden nur mit --print-folders ausgegeben (Spezialordner immer).

Nur Standardbibliothek, Python >= 3.10.
"""
from __future__ import annotations

import argparse
import base64
import email.utils
import imaplib
import json
import os
import re
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from typing import Any, Callable

IMAP_HOST = "imap.gmx.net"
IMAP_PORT = 993
SMTP_HOST = "mail.gmx.net"
SMTP_PORT = 587
TIMEOUT = 30
MAX_PARALLEL = 4
# Neuere imaplib-Versionen setzen Mailbox-Namen selbst in Anfuehrungszeichen (_astring), aeltere nicht.
AUTO_QUOTE = hasattr(imaplib.IMAP4, "_astring")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# --------------------------------------------------------------------------- helpers


def utf7_encode(name: str) -> str:
    """Modified UTF-7 (RFC 3501 section 5.1.3) for IMAP mailbox names."""
    out: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            raw = "".join(buf).encode("utf-16-be")
            enc = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
            out.append("&" + enc + "-")
            buf.clear()

    for ch in name:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


def utf7_decode(name: str) -> str:
    """Decode modified UTF-7 mailbox names."""

    def repl(match: re.Match[str]) -> str:
        chunk = match.group(1)
        if chunk == "":
            return "&"
        b64 = chunk.replace(",", "/")
        b64 += "=" * (-len(b64) % 4)
        return base64.b64decode(b64).decode("utf-16-be")

    return re.sub(r"&([^-]*)-", repl, name)


def quote(mailbox: str) -> str:
    """Quote an already UTF-7 encoded mailbox name (for raw commands)."""
    return '"' + mailbox.replace("\\", "\\\\").replace('"', '\\"') + '"'


def mb(mailbox: str) -> str:
    """Mailbox argument for imaplib high-level methods (version independent)."""
    return mailbox if AUTO_QUOTE else quote(mailbox)


def imap_date(ts: float) -> str:
    """IMAP date (dd-Mon-yyyy) independent of the system locale."""
    t = time.gmtime(ts)
    return f"{t.tm_mday:02d}-{MONTHS[t.tm_mon - 1]}-{t.tm_year}"


def decode_list(lines: list[Any]) -> list[dict[str, Any]]:
    """Parse LIST responses into dicts: flags, delimiter, name (decoded)."""
    out = []
    pat = re.compile(r'\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>.+)$')
    for line in lines:
        if line is None:
            continue
        if isinstance(line, tuple):  # literal form
            line = line[0] + b" " + line[1]
        text = line.decode("utf-8", errors="replace")
        m = pat.match(text.strip())
        if not m:
            continue
        name = m.group("name").strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        delim = m.group("delim")
        out.append(
            {
                "flags": m.group("flags").split(),
                "delimiter": None if delim == "NIL" else delim.strip('"'),
                "raw_name": name,
                "name": utf7_decode(name),
            }
        )
    return out


def err(e: BaseException) -> dict[str, str]:
    return {"error": f"{type(e).__name__}: {str(e)[:160]}"}


def parse_uid(data: Any, kind: str) -> list[int]:
    """Extract UIDs from [APPENDUID v u] / [COPYUID v src dst] responses."""
    text = b" ".join(d if isinstance(d, bytes) else b"" for d in (data or [])).decode(
        "utf-8", errors="replace"
    )
    m = re.search(rf"{kind}\s+\d+\s+(?:[\d:,*]+\s+)?([\d:,]+)", text)
    if not m:
        return []
    uids: list[int] = []
    for part in m.group(1).split(","):
        if ":" in part:
            a, b = part.split(":")
            uids.extend(range(int(a), int(b) + 1))
        elif part.isdigit():
            uids.append(int(part))
    return uids


def make_message(msg_id: str, user: str, subject: str, text: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = user
    msg["Subject"] = subject
    msg["Message-ID"] = msg_id
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg.set_content(text)
    return msg.as_bytes()


class Probe:
    def __init__(self, user: str, password: str, args: argparse.Namespace) -> None:
        self.imap_host = args.imap_host
        self.imap_port = args.imap_port
        self.user = user
        self.password = password
        self.args = args
        self.result: dict[str, Any] = {}
        self.ctx = ssl.create_default_context()
        self.ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        self.token = f"haprobe-{int(time.time())}"
        self.M: imaplib.IMAP4_SSL | None = None
        self.delim = "/"
        self.caps: set[str] = set()
        self.folder_names: set[str] | None = None
        self.special: dict[str, str] = {}
        self.created: list[str] = []  # UTF-7 names of test folders we created (for cleanup)
        self.a = ""
        self.b = ""

    def step(self, name: str, fn: Callable[[], Any]) -> Any:
        try:
            value = fn()
        except Exception as e:  # noqa: BLE001 - we want every failure recorded, not raised
            value = err(e)
        if value is not None:
            self.result[name] = value
        return value

    # ---- IMAP -------------------------------------------------------------------

    def connect(self) -> bool:
        t0 = time.monotonic()
        try:
            if self.args.no_tls:
                M: Any = imaplib.IMAP4(self.imap_host, self.imap_port, timeout=TIMEOUT)
            else:
                M = imaplib.IMAP4_SSL(self.imap_host, self.imap_port, ssl_context=self.ctx, timeout=TIMEOUT)
        except Exception as e:  # noqa: BLE001
            self.result["connect"] = err(e)
            return False
        self.M = M
        info: dict[str, Any] = {}
        if not self.args.no_tls:
            sock = M.sock
            cert = sock.getpeercert() or {}
            cn = None
            for rdn in cert.get("subject", ()):
                for k, v in rdn:
                    if k == "commonName":
                        cn = v
            san = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]
            info = {
                "tls_version": sock.version(),
                "cipher": sock.cipher()[0] if sock.cipher() else None,
                "cert_cn": cn,
                "cert_san_matches_host": any(
                    s == self.imap_host or (s.startswith("*.") and self.imap_host.endswith(s[1:])) for s in san
                ),
            }
        info["greeting"] = M.welcome.decode("utf-8", errors="replace")[:80]
        info["capability_pre_login"] = sorted(M.capabilities)
        self.result["connect"] = info
        try:
            M.login(self.user, self.password)
        except imaplib.IMAP4.error as e:
            self.result["login"] = {
                "ok": False,
                "error": str(e)[:160],
                "hint": "App-Passwort korrekt? POP3/IMAP im Webmailer erlaubt (wird bei Nichtnutzung abgeschaltet)?",
            }
            return False
        self.result["login"] = {"ok": True, "seconds": round(time.monotonic() - t0, 2)}
        typ, data = M.capability()
        cap_line = data[0].decode() if data and data[0] else ""
        self.caps = set(cap_line.upper().split())
        self.result["capability_post_login"] = sorted(self.caps)
        return True

    def list_folders(self) -> None:
        M = self.M
        assert M is not None
        typ, lines = M.list()
        folders = decode_list(lines)
        if folders:
            self.delim = folders[0]["delimiter"] or "/"
        special: dict[str, str] = {}
        for f in folders:
            for fl in f["flags"]:
                if fl.lower() in {"\\sent", "\\drafts", "\\trash", "\\junk", "\\archive", "\\all", "\\flagged"}:
                    special[f["name"]] = fl
        ext: Any = None
        if "SPECIAL-USE" in self.caps and "LIST-EXTENDED" in self.caps:
            try:
                typ2, lines2 = M._simple_command("LIST", "(SPECIAL-USE)", '""', '"*"')  # noqa: SLF001
                typ2, lines2 = M._untagged_response(typ2, lines2, "LIST")  # noqa: SLF001
                ext = {f["name"]: [x for x in f["flags"] if x.startswith("\\")] for f in decode_list(lines2)}
            except Exception as e:  # noqa: BLE001
                ext = err(e)
        self.result["folders"] = {
            "count": len(folders),
            "delimiter": self.delim,
            "special_use_from_plain_list": special,
            "special_use_from_list_extended": ext,
        }
        if self.args.print_folders:
            self.result["folders"]["all_names"] = [f["name"] for f in folders]
        self.folder_names = {f["name"] for f in folders}
        self.special = special

    def status_inbox(self) -> Any:
        assert self.M
        typ, data = self.M.status("INBOX", "(MESSAGES UNSEEN UIDNEXT UIDVALIDITY)")
        return data[0].decode() if data and data[0] else None

    def uid_search(self, *crit: str, charset: str | None = None, literal: bytes | None = None) -> dict[str, Any]:
        assert self.M
        args = list(crit)
        if charset:
            args = ["CHARSET", charset, *args]
        if literal is not None:
            self.M.literal = literal  # appended as final literal argument by imaplib
        try:
            typ, data = self.M.uid("SEARCH", *args)
        except Exception as e:  # noqa: BLE001
            self.M.literal = None
            return err(e)
        found = data[0].split() if data and data[0] else []
        return {"status": typ, "hits": len(found)}

    def count_all(self) -> int:
        assert self.M
        typ, data = self.M.uid("SEARCH", "ALL")
        return len(data[0].split()) if data and data[0] else 0

    def create_test_folders(self) -> bool:
        assert self.M
        suffix = "Ä"  # Umlaut absichtlich: testet modifiziertes UTF-7
        a_plain = f"{self.args.folder_prefix}-{suffix}A"
        b_plain = f"{self.args.folder_prefix}-{suffix}B"
        if self.folder_names is None:
            self.result["test_folders"] = {"error": "Ordnerliste nicht verfuegbar - Abbruch (nichts veraendert)"}
            return False
        if a_plain in self.folder_names or b_plain in self.folder_names:
            self.result["test_folders"] = {"error": "Testordner existieren bereits - Abbruch (nichts veraendert)"}
            return False
        a, b = utf7_encode(a_plain), utf7_encode(b_plain)
        self.a, self.b = a, b
        res: dict[str, Any] = {}
        for label, name in (("A", a), ("B", b)):
            typ, data = self.M.create(mb(name))
            res[f"create_{label}"] = typ
            if typ != "OK":
                self.result["test_folders"] = res
                return False
            self.created.append(name)
        self.result["test_folders"] = res
        return True

    def run_folder_tests(self) -> None:
        M = self.M
        assert M is not None
        a, b = self.a, self.b
        tests: dict[str, Any] = {}
        self.result["folder_tests"] = tests
        id1 = f"<{self.token}-1@ha-probe.invalid>"
        id2 = f"<{self.token}-2@ha-probe.invalid>"
        m1 = make_message(id1, self.user, "HA Probe Grüße Äpfel", "Testkörper mit Umlauten: Größe Übung ß")
        m2 = make_message(id2, self.user, "HA Probe plain", "plain ascii body probe")
        typ, data = M.append(mb(a), r"(\Seen)", None, m1)
        tests["append_1"] = {"status": typ, "appenduid": parse_uid(data, "APPENDUID")}
        typ, data = M.append(mb(a), None, None, m2)
        tests["append_2"] = {"status": typ, "appenduid": parse_uid(data, "APPENDUID")}

        typ, data = M.select(mb(a))
        tests["select_a"] = typ
        pf = M.response("PERMANENTFLAGS")[1]
        tests["permanentflags"] = [x.decode("utf-8", errors="replace") for x in pf if x]
        tests["uidvalidity"] = [x.decode() for x in (M.response("UIDVALIDITY")[1] or []) if x]
        tests["count_a"] = self.count_all()

        s = {}
        s["ascii_subject_Probe"] = self.uid_search("SUBJECT", "Probe")
        s["ascii_subject_lowercase_probe"] = self.uid_search("SUBJECT", "probe")
        s["utf8_subject_Grüße"] = self.uid_search("SUBJECT", charset="UTF-8", literal="Grüße".encode("utf-8"))
        s["utf8_subject_gruesse_lowercase"] = self.uid_search("SUBJECT", charset="UTF-8", literal="grüße".encode("utf-8"))
        s["utf8_body_Übung"] = self.uid_search("BODY", charset="UTF-8", literal="Übung".encode("utf-8"))
        s["utf8_text_Größe"] = self.uid_search("TEXT", charset="UTF-8", literal="Größe".encode("utf-8"))
        s["no_charset_body_ascii"] = self.uid_search("BODY", "probe")
        s["no_charset_text_ascii"] = self.uid_search("TEXT", "ascii")
        s["header_message_id"] = self.uid_search("HEADER", "Message-ID", self.token)
        s["larger_10"] = self.uid_search("LARGER", "10")
        s["smaller_1000000"] = self.uid_search("SMALLER", "1000000")
        s["since_yesterday"] = self.uid_search("SINCE", imap_date(time.time() - 86400))
        s["seen"] = self.uid_search("SEEN")
        s["unseen"] = self.uid_search("UNSEEN")
        tests["search"] = s

        if "SORT" in self.caps:
            try:
                typ, data = M.uid("SORT", "(REVERSE DATE)", "UTF-8", "ALL")
                tests["sort"] = {"status": typ, "hits": len(data[0].split()) if data and data[0] else 0}
            except Exception as e:  # noqa: BLE001
                tests["sort"] = err(e)
        else:
            tests["sort"] = "capability missing"

        # Keywords
        uids = M.uid("SEARCH", "ALL")[1][0].split()
        first = uids[0].decode() if uids else None
        if first:
            try:
                typ, data = M.uid("STORE", first, "+FLAGS", "(haprobe)")
                kw = self.uid_search("KEYWORD", "haprobe")
                tests["custom_keyword"] = {"store": typ, "keyword_search": kw}
            except Exception as e:  # noqa: BLE001
                tests["custom_keyword"] = err(e)
            try:
                typ, data = M.uid("STORE", first, "+FLAGS", r"(\Flagged \Answered)")
                tests["system_flags_store"] = typ
            except Exception as e:  # noqa: BLE001
                tests["system_flags_store"] = err(e)

        # COPY A -> B
        try:
            typ, data = M._simple_command("UID", "COPY", first or "1", quote(b))  # noqa: SLF001 (raw: keeps COPYUID)
            tests["copy_to_b"] = {"status": typ, "copyuid_seen": bool(parse_uid(data, "COPYUID"))}
        except Exception as e:  # noqa: BLE001
            tests["copy_to_b"] = err(e)
        # MOVE A -> B (second message)
        if "MOVE" in self.caps and len(uids) > 1:
            try:
                typ, data = M._simple_command("UID", "MOVE", uids[1].decode(), quote(b))  # noqa: SLF001
                tests["move_to_b"] = {"status": typ, "copyuid_seen": bool(parse_uid(data, "COPYUID"))}
            except Exception as e:  # noqa: BLE001
                tests["move_to_b"] = err(e)
        else:
            tests["move_to_b"] = "skipped (no MOVE capability or message missing)"
        tests["count_a_after"] = self.count_all()
        typ, data = M.status(mb(b), "(MESSAGES)")
        tests["status_b"] = data[0].decode("utf-8", errors="replace") if data and data[0] else None

        # Delete semantics inside test folder B: \Deleted + UID EXPUNGE
        typ, data = M.select(mb(b))
        b_uids = M.uid("SEARCH", "ALL")[1][0].split()
        if b_uids:
            M.uid("STORE", b_uids[0].decode(), "+FLAGS", r"(\Deleted)")
            tests["deleted_flag_visible"] = self.uid_search("DELETED")
            if "UIDPLUS" in self.caps:
                try:
                    typ, data = M.uid("EXPUNGE", b_uids[0].decode())
                    tests["uid_expunge"] = {"status": typ, "count_b_after": self.count_all()}
                except Exception as e:  # noqa: BLE001
                    tests["uid_expunge"] = err(e)
            else:
                tests["uid_expunge"] = "skipped (no UIDPLUS)"
        M.close()

    def cleanup_test_folders(self) -> None:
        M = self.M
        if M is None or not self.created:
            return
        res: dict[str, Any] = {}
        for name in list(self.created):
            try:
                typ, _ = M.select(mb(name))
                if typ == "OK":
                    ids = M.uid("SEARCH", "ALL")[1][0].split()
                    for u in ids:
                        M.uid("STORE", u.decode(), "+FLAGS", r"(\Deleted)")
                    if ids:
                        M.expunge()  # nur in unserem eigenen Testordner
                    M.close()
            except Exception as e:  # noqa: BLE001
                res["clear_folder"] = err(e)
        if self.a in self.created:
            try:
                new_a = self.a + "x"
                typ, _ = M.rename(mb(self.a), mb(new_a))
                res["rename_folder"] = typ
                if typ == "OK":
                    self.created[self.created.index(self.a)] = new_a
                    self.a = new_a
            except Exception as e:  # noqa: BLE001
                res["rename_folder"] = err(e)
        for i, name in enumerate(list(self.created)):
            try:
                typ, _ = M.delete(mb(name))
                res[f"delete_folder_{i + 1}"] = typ
            except Exception as e:  # noqa: BLE001
                res[f"delete_folder_{i + 1}"] = err(e)
        self.result["cleanup"] = res

    def parallel_connections(self) -> dict[str, Any]:
        conns = []
        out: dict[str, Any] = {"requested_extra": min(self.args.parallel, MAX_PARALLEL - 1)}
        for i in range(out["requested_extra"]):
            try:
                if self.args.no_tls:
                    c = imaplib.IMAP4(self.imap_host, self.imap_port, timeout=20)
                else:
                    c = imaplib.IMAP4_SSL(self.imap_host, self.imap_port, ssl_context=self.ctx, timeout=20)
                c.login(self.user, self.password)
                conns.append(c)
            except Exception as e:  # noqa: BLE001
                out[f"connection_{i + 2}"] = err(e)
                break
        out["parallel_ok_including_main"] = len(conns) + 1
        for c in conns:
            try:
                c.logout()
            except Exception:  # noqa: BLE001
                pass
        return out

    # ---- SMTP -------------------------------------------------------------------

    def smtp_probe(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        S = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT)
        try:
            S.ehlo()
            out["ehlo_pre_tls"] = sorted(S.esmtp_features.keys())
            S.starttls(context=self.ctx)
            S.ehlo()
            out["ehlo_post_tls"] = sorted(S.esmtp_features.keys())
            out["size_limit"] = S.esmtp_features.get("size")
            S.login(self.user, self.password)
            out["login"] = "ok"
        finally:
            try:
                S.quit()
            except Exception:  # noqa: BLE001
                pass
        return out

    def send_self_test(self) -> dict[str, Any]:
        """Sendet GENAU EINE Mail an sich selbst und prueft die Gesendet-Kopie. Loescht die Testmails danach."""
        M = self.M
        assert M is not None
        out: dict[str, Any] = {}
        send_token = f"{self.token}-send"
        msg_id = f"<{send_token}@ha-probe.invalid>"
        msg = EmailMessage()
        msg["From"] = self.user
        msg["To"] = self.user
        msg["Subject"] = "HA Probe Selbsttest"
        msg["Message-ID"] = msg_id
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg.set_content("Selbsttest, kann geloescht werden.")
        S = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT)
        try:
            S.ehlo()
            S.starttls(context=self.ctx)
            S.ehlo()
            S.login(self.user, self.password)
            refused = S.send_message(msg)
            out["smtp_send"] = {"refused": len(refused)}
        finally:
            try:
                S.quit()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(self.args.send_wait)

        sent_names = [n for n, fl in getattr(self, "special", {}).items() if fl.lower() == "\\sent"]
        if not sent_names:
            sent_names = [n for n in getattr(self, "folder_names", set()) if n.lower() in {"gesendet", "sent"}]
        counts: dict[str, Any] = {}
        for label, name in [("INBOX", "INBOX")] + [(f"sent[{i}]", n) for i, n in enumerate(sent_names)]:
            try:
                typ, _ = M.select(mb(utf7_encode(name)) if name != "INBOX" else "INBOX")
                if typ != "OK":
                    counts[label] = {"select": typ}
                    continue
                typ, data = M.uid("SEARCH", "HEADER", "Message-ID", send_token)
                ids = data[0].split() if data and data[0] else []
                counts[label] = {"copies_found": len(ids)}
                for u in ids:  # nur unsere eigenen Testmails loeschen
                    M.uid("STORE", u.decode(), "+FLAGS", r"(\Deleted)")
                    if "UIDPLUS" in self.caps:
                        M.uid("EXPUNGE", u.decode())
                    else:
                        counts[label]["note"] = "no UIDPLUS: marked \\Deleted only (not expunged)"
                M.close()
            except Exception as e:  # noqa: BLE001
                counts[label] = err(e)
        out["copies"] = counts
        out["interpretation"] = (
            "sent_copy_mode: 0 Kopien in Gesendet -> 'append' noetig; 1 Kopie -> 'none'/'server'; mehr -> Duplikate beachten"
        )
        return out

    # ---- main -------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        self.result["meta"] = {
            "script": "gmx_probe.py",
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "python": sys.version.split()[0],
            "token_prefix": self.token,
        }
        if not self.connect():
            return self.result
        assert self.M is not None
        try:
            self.step("namespace", lambda: self.M.namespace()[1][0].decode() if "NAMESPACE" in self.caps else "capability missing")  # type: ignore[union-attr]
            self.step("list", self.list_folders)
            self.step("status_inbox", self.status_inbox)
            if self.step("create_test_folders", self.create_test_folders) is True:
                self.step("folder_tests_run", self.run_folder_tests)
            if not self.args.no_parallel:
                self.step("parallel_connections", self.parallel_connections)
            if not self.args.skip_smtp:
                self.step("smtp", self.smtp_probe)
            if self.args.send_self_test and not self.args.skip_smtp:
                self.step("send_self_test", self.send_self_test)
            self.step("manage_sieve_port_4190", self.sieve_port)
        finally:
            try:
                self.cleanup_test_folders()
            finally:
                try:
                    self.M.logout()
                except Exception:  # noqa: BLE001
                    pass
        return self.result

    def sieve_port(self) -> dict[str, Any]:
        import socket

        res = {}
        for host in (IMAP_HOST, SMTP_HOST):
            try:
                with socket.create_connection((host, 4190), timeout=5) as s:
                    s.settimeout(3)
                    banner = s.recv(64).decode("utf-8", errors="replace")
                res[host] = {"open": True, "banner_prefix": banner[:30]}
            except Exception as e:  # noqa: BLE001
                res[host] = {"open": False, "reason": type(e).__name__}
        return res


def main() -> int:
    ap = argparse.ArgumentParser(description="GMX Live-Probe (Metadaten only)")
    ap.add_argument("--print-folders", action="store_true", help="alle Ordnernamen ausgeben")
    ap.add_argument("--send-self-test", action="store_true", help="genau 1 Mail an sich selbst senden und Gesendet-Kopie pruefen")
    ap.add_argument("--send-wait", type=int, default=10, help="Sekunden Wartezeit nach dem Senden (Default 10)")
    ap.add_argument("--no-parallel", action="store_true", help="Test paralleler Verbindungen auslassen")
    ap.add_argument("--parallel", type=int, default=3, help="zusaetzliche parallele Verbindungen (max 3)")
    ap.add_argument("--folder-prefix", default="HA-Probe", help="Praefix der Testordner")
    ap.add_argument("--imap-host", default=IMAP_HOST, help="nur fuer lokale Tests (Dovecot/GreenMail); Default imap.gmx.net")
    ap.add_argument("--imap-port", type=int, default=IMAP_PORT, help="nur fuer lokale Tests")
    ap.add_argument("--no-tls", action="store_true", help="Klartext-IMAP, nur fuer lokale Tests; nie gegen GMX")
    ap.add_argument("--skip-smtp", action="store_true", help="SMTP-Teil auslassen")
    args = ap.parse_args()

    if args.no_tls and args.imap_host.endswith("gmx.net"):
        print(json.dumps({"error": "--no-tls ist gegen GMX nicht erlaubt"}), file=sys.stderr)
        return 2
    user = os.environ.get("GMX_USER", "")
    password = os.environ.get("GMX_PASS", "")
    if not user or not password:
        print(json.dumps({"error": "GMX_USER und GMX_PASS muessen als Umgebungsvariablen gesetzt sein"}), file=sys.stderr)
        return 2

    result = Probe(user, password, args).run()
    print(json.dumps(result, indent=1, ensure_ascii=False, default=str))
    return 0 if result.get("login", {}).get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
