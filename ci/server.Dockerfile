ARG BASE_IMAGE=ubuntu:22.04
FROM ${BASE_IMAGE}
ENV container=docker
COPY server_deploy/dependencies.sh /fixture/dependencies.sh
COPY ci/server_image.sh /fixture/server_image.sh
RUN GITHUB_ACTIONS=true bash /fixture/server_image.sh
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
