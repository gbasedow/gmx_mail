# Staging-Test (echtes Home Assistant im Container)

Prüft die Integration Ende-zu-Ende in der Ziel-Version von Home Assistant (2026.11.2) – nur über die öffentlichen
HA-APIs (REST und WebSocket), so wie ha-mcp die Aktionen aufruft. Mailserver ist der GMX-nahe Dovecot mit Test-CA.
Kein echtes Postfach, keine Änderung an der produktiven Instanz.

```bash
python3 tools/make_test_certs.py
pip download --no-deps -d tests/staging/wheels imapclient==4.3.0
docker compose --profile staging down -v
docker compose up -d dovecot
docker compose --profile staging up -d --build homeassistant
pytest -q -p no:homeassistant -m staging tests/staging
```

- `down -v` setzt den HA-Zustand zurück (der Test führt das Onboarding selbst durch).
- Das Image `homeassistant/home-assistant` kommt von Docker Hub; `imapclient` wird aus dem vorab geladenen Wheel installiert,
  damit der Container-Build keinen Internetzugang braucht.
- `REQUESTS_CA_BUNDLE` lässt HA nur der Test-CA vertrauen – ausschließlich im Staging-Container.
- Ablauf: Onboarding → Config-Flow (TLS, IMAP- und SMTP-Login) → Entitäten und Push-Status → Leseaktionen mit
  `return_response` → neue Mail → IDLE → Ereignis `gmx_mail_new_message` → Suche/Lesen ohne `\Seen` → Diagnose ohne Zugangsdaten.
