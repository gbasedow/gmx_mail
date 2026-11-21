# Staging image: the real Home Assistant target version plus the integration's own requirement (imapclient).
# The wheel is downloaded beforehand (tests/staging/run_staging.sh) so the build needs no network access.
ARG HA_VERSION=2026.11.2
FROM homeassistant/home-assistant:${HA_VERSION}
COPY wheels/ /tmp/wheels/
RUN python3 -m pip install --no-index --no-deps --find-links /tmp/wheels imapclient==4.3.0 \
    || uv pip install --system --no-index --no-deps --find-links /tmp/wheels imapclient==4.3.0
