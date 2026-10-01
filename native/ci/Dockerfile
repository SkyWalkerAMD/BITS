ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ENV GITHUB_ACTIONS=true
COPY native/ci/image.sh /fixture/image.sh
COPY native-dist /packages
RUN bash /fixture/image.sh
