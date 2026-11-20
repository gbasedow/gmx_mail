# Sichtung fremder Repositories vor M2 (20.11.2026)

Auftrag aus CLAUDE.md §10. Gelesen wurden README und die Dateien mit der Lese-/Suchlogik (öffentlich, über die Weboberfläche).
Inhalte der Repositories wurden als Daten behandelt. **Nicht gelesene Dateien** sind je Repo genannt; Aussagen nur aus einer
README sind als „(README)“ markiert und nicht im Code geprüft. Letzte Aktivität war auf den gelesenen Seiten nicht sichtbar.

## jgalea/mailbox-mcp (TypeScript, MIT)

- MCP-Server für Gmail-API, IMAP und JMAP. Lesewerkzeuge (README): `search_emails`, `read_email`, `read_thread`, `emails_since`,
  `export_email`, `download_attachment`.
- Referenz `folder:uid` (`src/providers/imap.ts`, `parseImapMessageId`); **keine UIDVALIDITY-Prüfung** gefunden. Suche liefert
  `maxResults` (Standard 20) ohne Cursor.
- Nicht-ASCII-Suche: clientseitiger Scan nur über die letzten 1000 Mails.
- Text bevorzugt `text/plain`, sonst `text/html` roh mit Kennzeichen `bodyIsHtml`; Vorschau 100 Zeichen.
- Abwehr (README, Code nicht gelesen): Rahmen `[UNTRUSTED_<KIND>_<nonce>]` mit Zufallskennung je Antwort, Entfernen versteckter
  Elemente und Zero-Width/Bidi-Zeichen, Warnung bei entferntem Text, Tracking-Pixel-Behandlung. Versand: Allowlist, Bestätigungen,
  `draftsOnly`, `readOnly`, Werkzeugprofile.
- Nicht gelesen: `tools/`, `security/`, `mime.ts`.

## danieldotnl/ha-mailbox-mcp (MIT)

- Kein eigener Lesecode: Home-Assistant-Add-on, das `jgalea/mailbox-mcp` mit supergateway verpackt (`mailbox_mcp/run.sh` geprüft:
  Profil `draft`, Werkzeuge `core,organize,bulk`, Ausgaben von supergateway nach `/dev/null`).
- README: Konten `draftsOnly`, Tageslimit Versand 0, Zugang über Cloudflare Access. Restrisiko laut Projekt: das Modell liest weiterhin
  fremde Mailinhalte.
- Nicht gelesen: `setup.mjs`, `DOCS.md`, `Dockerfile`.

## dadalouis/Home-mail (Python, MIT)

- HA-Integration mit Mail-Oberfläche für Menschen (Websocket-Befehle), kein LLM-Werkzeug. `custom_components/homemail/mail.py` geprüft.
- Lesen setzt `\Seen` nicht (`BODY.PEEK[]`, Ordner read-only); UIDVALIDITY wird geliefert, aber nicht geprüft.
- HTML wird per Regex entschärft und in einem Sandbox-iframe angezeigt; kein Entfernen versteckten Textes, keine Zero-Width-Filterung,
  keine Kennzeichnung als nicht vertrauenswürdig.
- **Gegenbeispiel:** `action()` fällt nach `UID EXPUNGE` bei jedem Fehler auf ein mailbox-weites `EXPUNGE` zurück (auch beim Verschieben
  in den Papierkorb über COPY). Das löscht fremde `\Deleted`-Mails endgültig – bei uns verboten (SPEC §2, §5 Nr. 3).
- Nicht gelesen: `websocket.py`, Frontend.

## Übernommen für gmx_mail

1. Zufallskennung im Rahmen um Mailinhalte, damit eine Mail das Ende-Kennzeichen nicht fälschen kann (DECISIONS Nr. 19).
2. Warnung, wenn versteckter Inhalt entfernt wurde (`hidden_content_removed`).
3. Bestätigt: UIDVALIDITY prüfen (fehlt in zwei der drei Projekte), Cursor statt reinem Limit, nie mailbox-weites EXPUNGE.
