ARG BASE_IMAGE
FROM ${BASE_IMAGE}
RUN if command -v dnf >/dev/null; then \
      if grep -q 'VERSION_ID="8' /etc/os-release; then dnf -y install platform-python tar gzip; \
      else dnf -y install python3 tar gzip; fi; \
    else apt-get update && apt-get install -y --no-install-recommends python3 python3-venv tar gzip ca-certificates; fi
