FROM debian:bookworm-slim

ARG BUILD_ARCH
ARG BUILD_VERSION

LABEL io.hass.type="addon" \
      io.hass.arch="${BUILD_ARCH}" \
      io.hass.version="${BUILD_VERSION}"

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WYOMING_WHISPER_CPP_VERSION=1.1.0

WORKDIR /usr/src
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        python3 \
        python3-pip \
    && curl --fail --location --silent --show-error \
        "https://github.com/rhasspy/wyoming-whisper-cpp/archive/refs/tags/v${WYOMING_WHISPER_CPP_VERSION}.tar.gz" \
        | tar -xz -C /tmp \
    && mv "/tmp/wyoming-whisper-cpp-${WYOMING_WHISPER_CPP_VERSION}" /usr/share/wyoming-whisper-cpp \
    && make -C /usr/share/wyoming-whisper-cpp/whisper.cpp main CPUINFO_CMD=true \
    && /usr/share/wyoming-whisper-cpp/whisper.cpp/main -h >/dev/null \
    && python3 -m pip install --no-cache-dir --upgrade setuptools wheel \
    && python3 -m pip install --no-cache-dir /usr/share/wyoming-whisper-cpp \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY run.sh /run.sh
RUN chmod 0755 /run.sh

EXPOSE 10301
CMD ["/run.sh"]
