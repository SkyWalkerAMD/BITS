ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG PACKAGE_DIR=native-dist
ENV GITHUB_ACTIONS=true
COPY native/ci/image.sh /fixture/image.sh
COPY ${PACKAGE_DIR} /packages
RUN bash /fixture/image.sh
