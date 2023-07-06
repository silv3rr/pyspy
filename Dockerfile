# syntax=docker/dockerfile:1
FROM python:3.9.17-bookworm
LABEL org.opencontainers.image.source=https://github.com/silv3rr/pyspy
WORKDIR /app
COPY requirements.txt requirements.txt
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
# hadolint ignore=DL3013
RUN pip3 install --no-cache-dir -r requirements.txt && \
    pip3 install --no-cache-dir geoip2 flask
COPY . .
ENTRYPOINT ["./spy.py"]
