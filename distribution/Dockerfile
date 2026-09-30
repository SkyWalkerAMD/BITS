ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY server_deploy/dependencies.sh /fixture/dependencies.sh
COPY ci/server_image.sh /fixture/server_image.sh
COPY distribution/image.sh /fixture/native.sh
COPY native-input /root/native-packages
RUN GITHUB_ACTIONS=true bash /fixture/server_image.sh && bash /fixture/native.sh
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
