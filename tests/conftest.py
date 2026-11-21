"""Split the test suite into two runs.

* ``tests/gmx_mail``: the integration, needs the ``homeassistant`` pytest plugin
  (pytest-homeassistant-custom-component), which blocks sockets and checks for lingering threads.
* ``tests/probe``: the stand-alone live-probe script against a local fake IMAP server (uses sockets and
  threads), run with ``-p no:homeassistant``.
* ``tests/integration``: read operations against GreenMail in a container (``docker compose up -d greenmail``),
  run with ``-p no:homeassistant -m integration tests/integration``; skipped if the server is not reachable.

Each part is skipped (not collected) in the other run; ``pytest`` prints which part was ignored.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_HERE = Path(__file__).parent
_PROBE = _HERE / "probe"
_SERVER = _HERE / "integration"  # real IMAP server in a container (sockets), like the probe tests
_STAGING = _HERE / "staging"  # real Home Assistant in a container (HTTP)
_INTEGRATION = _HERE / "gmx_mail"


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """Ignore the part of the suite that cannot run with the current plugin set."""
    ha_plugin = config.pluginmanager.hasplugin("homeassistant")
    if ha_plugin and collection_path in (_PROBE, _SERVER, _STAGING):
        return True
    if not ha_plugin and collection_path == _INTEGRATION:
        return True
    return None


def pytest_report_header(config: pytest.Config) -> str:
    """Tell which part of the suite runs."""
    if config.pluginmanager.hasplugin("homeassistant"):
        return "gmx_mail: HA tests (probe/server tests: pytest -p no:homeassistant tests/probe tests/integration)"
    return "gmx_mail: probe and server tests only (tests/gmx_mail needs the homeassistant plugin)"
