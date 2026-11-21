"""Where saved attachments live and how old ones are removed (DECISIONS Nr. 42).

Only files in the account's own directory ``<media>/gmx_mail/<entry_id>/`` are ever deleted.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

MEDIA_SUBDIR = "gmx_mail"


def attachment_dir(hass: HomeAssistant, entry_id: str) -> Path:
    """Return ``<media>/gmx_mail/<entry_id>`` (HAOS: /media, containers: config/media)."""
    root = Path(hass.config.media_dirs.get("local") or hass.config.path("media"))
    return root / MEDIA_SUBDIR / entry_id


def remove_old_files(directory: Path, max_age_days: int, now: float | None = None) -> int:
    """Delete regular files older than ``max_age_days`` directly in ``directory`` (blocking). 0 days = keep all."""
    if max_age_days <= 0 or not directory.is_dir():
        return 0
    cutoff = (now if now is not None else time.time()) - max_age_days * 86400
    removed = 0
    for path in directory.iterdir():
        # No recursion and no symlinks: only the files this integration wrote itself.
        if path.is_symlink() or not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError as err:
            _LOGGER.debug("Could not remove an old attachment: %s", type(err).__name__)
    return removed
