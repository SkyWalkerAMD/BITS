#!/bin/bash
set -euo pipefail
[[ ${GITHUB_ACTIONS:-} == true ]]
mkdir -p .independent-results
docker pull "$BASE_IMAGE"
digest=$(docker image inspect "$BASE_IMAGE" --format '{{index .RepoDigests 0}}')
printf '%s\n' "$digest" > .independent-results/image.txt
if [[ $BASE_IMAGE == debian:11 ]]; then
    python3 bits_core/workloads/debian11_ci.py independent bits-independent-test native-dist "$digest" 2>&1 | tee .independent-results/install.txt
else
    docker build --progress plain --build-arg BASE_IMAGE="$digest" -f native/ci/Dockerfile -t bits-independent-test . 2>&1 | tee .independent-results/install.txt
fi
docker run -d --name bits-independent-test --hostname BITS-CLOUD --cpus=2 --memory=3g --pids-limit=512 \
    -e container=docker --privileged --cgroupns=private \
    --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
    --tmpfs /run --tmpfs /run/lock --tmpfs /tmp:rw,exec,nosuid,nodev,mode=1777 \
    -v "$PWD:/src:ro" -v "$PWD/.independent-results:/results" \
    bits-independent-test /bin/bash -c 'mount -o remount,rw /sys/fs/cgroup; exec /sbin/init'
for attempt in $(seq 1 60); do
    if docker exec bits-independent-test test -d /run/systemd/system; then break; fi
    sleep 1
done
docker exec bits-independent-test test -d /run/systemd/system
address=$(docker inspect bits-independent-test --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
if [[ $BASE_IMAGE == rockylinux/rockylinux:8 || $BASE_IMAGE == ubuntu:22.04 ]]; then
    docker exec -e GITHUB_ACTIONS=true bits-independent-test python3 -I -B /src/native/ci/interface_tests.py 2>&1 | tee .independent-results/interface-tests.txt
fi
docker exec -e GITHUB_ACTIONS=true bits-independent-test python3 -I -B /src/native/ci/acceptance.py "$address" 2>&1 | tee .independent-results/test.txt
