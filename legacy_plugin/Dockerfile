ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY bits_core/center/dependencies.sh /fixture/dependencies.sh
COPY ci/server_image.sh /fixture/server_image.sh
COPY legacy-dist /root/plugin-packages
RUN GITHUB_ACTIONS=true bash /fixture/server_image.sh && \
    if command -v dnf >/dev/null; then \
      dnf -y install /root/plugin-packages/bits-o-control*.rpm /root/plugin-packages/bits-o-workloads*.rpm iputils dmidecode shadow-utils; \
    else \
      apt-get update && apt-get install -y /root/plugin-packages/bits-o-control*.deb /root/plugin-packages/bits-o-workloads*.deb iputils-ping dmidecode passwd; \
    fi
