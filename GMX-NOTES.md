# GMX-NOTES.md – GMX-Anforderungen und -Eigenheiten

Stand der Recherche: 20.11.2026. Quellen: siehe `docs/SOURCES.md`.

Verlässlichkeit: **O** = offiziell (GMX-Hilfe/GMX-Seite), **D** = Drittquelle (Forum, Issue, Log, Presse), **U** = unbestätigt.
**LIVE** = nur durch Test gegen ein echtes GMX-Konto klärbar. Spalte „Ergebnis“ nach M0 ausfüllen.

GMX bietet nur IMAP und SMTP. Eine dokumentierte Mail-API, ManageSieve oder IMAP-Zugriff auf die Webmail-Filterregeln
wurde nicht gefunden. „Vollständiges Management“ bedeutet hier: alles, was IMAP und SMTP hergeben.

## 1. Anforderungen und Eigenheiten

| # | Thema | Befund | Quelle | Verl. | LIVE | Ergebnis |
|---|---|---|---|---|---|---|
| 1 | IMAP | `imap.gmx.net:993`, implizites TLS; Fallback Port 143 mit STARTTLS (sonst unverschlüsselt) | hilfe.gmx.net/pop-imap/imap/imap-serverdaten.html | O | – | |
| 2 | SMTP | `mail.gmx.net:587` STARTTLS; alternativ 465 SSL | dto. | O | – | |
| 3 | TLS | nur TLS 1.2/1.3; 1.0/1.1 nicht mehr unterstützt | dto. | O | Zertifikat/CN prüfen | |
| 4 | Login | Benutzername = volle E-Mail-Adresse (auch bei @gmx.de) | GMX-Hilfe (Outlook/Live Mail), Drittquellen | O/D | – | |
| 5 | Aktivierung | POP3/IMAP ab Werk aus. Einschalten: Initialen > E-Mail-Einstellungen > E-Mail empfangen > POP3/IMAP > „POP3- und IMAP-Zugriff erlauben“ | hilfe.gmx.net/pop-imap/einschalten.html | O | – | |
| 6 | Auto-Abschaltung | „Wenn Sie das E-Mail-Programm längere Zeit nicht verwenden, wird der Abruf über POP3 bzw. IMAP … automatisch ausgeschaltet“. Keine Frist genannt. Wiedereinschalten: „die zwei angezeigten Symbole übereinander schieben“ (interaktiv, nicht automatisierbar). Symptom beim Client: „Kennwort falsch“. Forum: Abschaltung „alle 4 Wochen“ trotz täglichem Abruf; GMX-Mail „Abschaltung POP/IMAP zum 09.01.2024“ mit Hinweis, man müsse in der GMX Mail App angemeldet sein. Ob ein Dauer-IDLE als Nutzung zählt, ist zweifelhaft. | hilfe.gmx.net/pop-imap/kein-versand-mailprogramm.html; heise; thunderbird-mail.de Thread 93637 | O/D | Frist unbekannt | |
| 7 | App-Passwort | Account verwalten > Login & Sicherheit > App-Passwörter (für externe Apps). Einmalige Anzeige, löschbar, neu erstellbar. Gilt auch für CalDAV/CardDAV/WebDAV (Scope nicht nur Mail). | hilfe.gmx.net/sicherheit/2fa/anwendungsspezifisches-passwort.html; gmx.net/mail/sicherheit/zwei-faktor-authentifizierung/ | O | Anzahl, Fehlertext | |
| 8 | 2FA | Mit aktiver 2FA ist für POP3/IMAP nur ein App-Passwort möglich | gmx.net 2FA-Seite | O | – | |
| 9 | Länder | Zugriff außerhalb Europas/USA/Kanada nur mit App-Passwort | hilfe.gmx.net/pop-imap/kein-versand-mailprogramm.html | O | – | |
| 10 | CAPABILITY (vor Login) | `IMAP4rev1 CHILDREN ENABLE ID IDLE LIST-EXTENDED LIST-STATUS LITERAL- MOVE NAMESPACE SASL-IR SORT SPECIAL-USE THREAD=ORDEREDSUBJECT UIDPLUS UNSELECT WITHIN` plus `STARTTLS LOGINDISABLED` (143) bzw. `AUTH=LOGIN AUTH=PLAIN` (TLS) | eM-Client-Forum 11/2022; mailcow Issue #5778 (03/2024); Pegasus-Mail-Forum 12/2025 (nur Snippet) | D | Liste nach Login | |
| 11 | Fehlende Capabilities | kein CONDSTORE/QRESYNC, QUOTA, ESEARCH, UTF8=ACCEPT, COMPRESS, THREAD=REFERENCES, X-GM-EXT | dto. (Abwesenheit im Banner) | D | ja | |
| 12 | Historie | 2009: nur `IDLE UNSELECT LITERAL+`; UIDPLUS funktionierte, wurde aber nicht gemeldet | bugs.kde.org 204653 | D | – | |
| 13 | Server-Software | Begrüßung früher „GMX IMAP4 StreamProxy“, heute „IMAP server ready H migmx…“ | KDE-Bug, Foren | D | – | |
| 14 | Ordner (IMAP) | `Gesendet`, `Entwürfe`, `Gelöscht`, `Spamverdacht` (Live-Mail-Anleitung); neuere Hilfe spricht von „Papierkorb“/„Spam“; Thunderbird legte früher zusätzlich `Sent` an. **Quellen widersprechen sich.** | hilfe.gmx.net/pop-imap/imap/livemail.html; hilfe.gmx.net/email/ordner/systemordner.html; heise | O (Widerspruch) | SPECIAL-USE + Trennzeichen | |
| 15 | Umlaute in Ordnernamen | `Entwürfe` = modifiziertes UTF-7 `Entw&APw-rfe` (RFC 3501 5.1.3) | RFC 3501; bugzilla.mozilla.org 255541 | D | ja | |
| 16 | Papierkorb | Löschen -> Papierkorb; Löschen im Papierkorb = endgültig; Aufbewahrung „standardmäßig unbegrenzt“ (älteres Drittzitat: 1 Tag) | hilfe.gmx.net/email/verwalten-und-suchen/emails-im-papierkorb.html | O | IMAP-Verhalten von `\Deleted` | |
| 17 | Spam | Aufbewahrung 30 Tage; Löschen dort „sofort endgültig“ | GMX-Hilfe „E-Mails nicht finden“, Papierkorb-Seite | O | – | |
| 18 | Ordnerfristen | pro Ordner einstellbar (Posteingang, Papierkorb, Spam, Gesendet, Entwürfe, eigene) | hilfe.gmx.net/email/ordner/aufbewahrungszeit-festlegen.html | O | – | |
| 19 | Anhänge | FreeMail 20 MB, ProMail 50 MB, TopMail 100 MB | hilfe.gmx.net/email/empfangen-und-lesen/dateianhaenge.html | O | SMTP-`SIZE` prüfen | |
| 20 | Versandlimits | Nicht veröffentlicht (PC-Welt 03/2022: „keine Auskunft“). JAM-Software (SmartSerialMail-FAQ): „dynamisch vom MailSecurity-Team angepasst“, „Maximal 11 Empfänger pro Stunde bei Verwendung einer E-Mail Client Anwendung“ – von GMX nicht bestätigt | pcwelt.de; manuals.jam-software.de | D/U | **nicht testen** (Sperrrisiko) | |
| 21 | Absender | Nur nachweislich eigene Adressen als Absender verwenden | imap-serverdaten.html | O | Alias-Versand | |
| 22 | Gesendet-Kopie | Unklar: Foren berichten fehlende **und** doppelte Kopien | heise; thunderbird-mail.de Thread 66525 | D/U | **Kernfrage** (`--send-self-test`) | |
| 23 | Verbindungslimit | Keine offizielle Zahl. Forum (2019): Standard von 5 Verbindungen funktioniert auch bei GMX | thunderbird-mail.de Thread 83066 | D/U | ja (vorsichtig, max. 4 parallel) | |
| 24 | Filterregeln/Sieve | Keine API/ManageSieve gefunden; Webmail-Filter per IMAP nicht sichtbar | keine Quelle | U | Port 4190 prüfen | |
| 25 | SEARCH | Verhalten bei `CHARSET UTF-8`, BODY/TEXT, Groß-/Kleinschreibung, Performance: keine Quelle. Warnbeispiel: Strato-IMAP lieferte seit Mitte 2026 für jede SEARCH ein leeres Ergebnis (imap-mcp-server Issue #138) | – | U | **ja** | |
| 26 | Flags | 2009: `PERMANENTFLAGS (\Answered \Flagged \Draft \Deleted \Seen)`, also kein `\*` = keine eigenen Keywords | bugs.kde.org 204653 | D (alt) | **ja** | |
| 27 | Quota | Keine QUOTA-Capability -> Speicherfüllstand per IMAP nicht abfragbar | siehe Zeile 11 | D | ja | |

### Widersprüche zwischen Quellen (nicht aufgelöst)

- Ordnernamen: `Gelöscht`/`Spamverdacht` (ältere Hilfe) gegen `Papierkorb`/`Spam` (neuere Hilfe). -> SPECIAL-USE nutzen, nie hart verdrahten.
- Papierkorb-Aufbewahrung: „unbegrenzt“ (offiziell, aktuell) gegen „1 Tag“ (älteres Drittzitat).
- Einführung von `smtp.send_message`: Aufgabenstellung nannte 2026.7, die HA-Aktionsdoku sagt „seit 2026.8“ (UI-Flow für SMTP seit 2026.7).

## 2. Konsequenzen für die Implementierung

1. Login-Fehler = „IMAP evtl. abgeschaltet **oder** Passwort falsch“: gemeinsamer Reauth-Pfad + Repair-Issue `imap_possibly_disabled` mit Anleitung.
2. Ordner über SPECIAL-USE erkennen; Heuristik und Options-Override als Rückfallebenen; Ordnernamen immer in modifiziertem UTF-7 kodieren/dekodieren.
3. SEARCH zuerst mit `CHARSET UTF-8` versuchen; bei Ablehnung Warnung und ASCII-Fallback. Bei leeren Ergebnissen trotz erwartbarer Treffer
   (Strato-Muster) clientseitigen Fallback über FETCH der Header erwägen – nach M0 entscheiden.
4. Kein QUOTA, kein CONDSTORE: kein Speichersensor, Änderungserkennung über UID/UIDVALIDITY und IDLE.
5. Eigene Keywords nur, wenn PERMANENTFLAGS `\*` enthält.
6. Gesendet-Kopie: Option `sent_copy_mode: auto|append|none`, Entscheidung nach dem Test (Zeile 22).
7. Nie mailbox-weites EXPUNGE; nur UID EXPUNGE mit UIDPLUS.
8. Konservative Versandlimits als Standard (Tageslimit, Empfänger pro Mail), unabhängig von GMX-Angaben.

## 3. Live-Testplan (M0)

Voraussetzungen: separates GMX-Testkonto (bevorzugt) oder eigenes Konto mit Vorsicht; POP3/IMAP freigeschaltet;
eigenes App-Passwort „probe“ (nach dem Test löschen); Python 3.10+ (nur Standardbibliothek).

```
export GMX_USER='testkonto@gmx.de'
export GMX_PASS='<App-Passwort>'
python3 tools/gmx_probe.py > gmx_probe_result.json
python3 tools/gmx_probe.py --send-self-test > gmx_probe_result_send.json   # optional, sendet genau 1 Mail an sich selbst
```

Stand der Prüfung des Skripts: Syntax, Hilfsfunktionen (`tests/test_gmx_probe_helpers.py`) und der Ablauf wurden gegen einen lokalen
Fake-IMAP-Server mit gestubbtem SMTP geprüft (Kommandosyntax, Parsing, Aufräumen, keine Ausgabe von Zugangsdaten). **Nicht** geprüft ist das
Verhalten gegen den echten GMX-Server; dort können Abweichungen auftreten (z. B. Fehlertexte, Antwortformate).

Das Skript:
- legt nur zwei eigene Testordner an (Name mit Umlaut) und bricht ab, falls sie schon existieren; es verändert keine vorhandenen Mails
  (Ausnahme `--send-self-test`: es löscht die beiden selbst erzeugten Testmails über ihre eindeutige Message-ID);
- gibt **nur Metadaten** aus: Capabilities, Statuscodes, Zähler, Zeiten, Namen der Spezialordner; alle Ordnernamen nur mit `--print-folders`;
- sendet im Standardlauf **keine** Mail (SMTP nur EHLO, STARTTLS und Login);
- druckt nie Passwort, Adressen, Betreffs oder Inhalte.

Manuelle Zusatztests (nicht vom Skript abgedeckt):
1. IDLE länger als 29 Minuten halten (Verbindung bleibt offen?).
2. Mail aus Spam per IMAP `MOVE` in den Papierkorb verschieben; Entwurf per APPEND mit `\Draft` anlegen und im Webmailer prüfen.
3. Port 4190 (ManageSieve) auf `mail.gmx.net` bzw. `imap.gmx.net` ansprechen.
4. Versand von einer Alias-Adresse testen (falls vorhanden).
5. Nach einigen Wochen prüfen, ob die IMAP-Abschaltung eintritt (Datum der letzten Nutzung notieren).

### Ergebnisfeld (nach dem Lauf ausfüllen)

```
Datum:
Konto-Typ (FreeMail/ProMail/TopMail):
CAPABILITY nach Login:
Spezialordner (Name -> Attribut):
Trennzeichen:
SEARCH UTF-8 Betreff / Body / TEXT:
PERMANENTFLAGS:
Gesendet-Kopie bei SMTP (0/1/2):
Parallele Verbindungen (OK bis):
Sonstiges:
```
