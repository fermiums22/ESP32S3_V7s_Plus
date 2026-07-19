FROM ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.24-2026.06.1

ARG BUILD_ARCH
ARG BUILD_VERSION

LABEL io.hass.type="addon" \
      io.hass.arch="${BUILD_ARCH}" \
      io.hass.version="${BUILD_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apk add --no-cache ca-certificates ffmpeg

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check -r /app/requirements.txt

COPY gopro_assist /app/gopro_assist
COPY run.sh /run.sh
RUN chmod 0755 /run.sh

CMD ["/run.sh"]
