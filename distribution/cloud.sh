#!/bin/bash
set -euo pipefail
[[ ${GITHUB_ACTIONS:-} == true ]]
mkdir -p .native-results
docker pull "$BASE_IMAGE"
digest=$(docker image inspect "$BASE_IMAGE" --format '{{index .RepoDigests 0}}')
printf '%s\n' "$digest" > .native-results/image.txt
if [[ $BASE_IMAGE == debian:11 ]]; then
    python3 bits_core/workloads/debian11_ci.py native ocrun-native-image native-input 2>&1 | tee .native-results/install.txt
else
    docker build --progress plain --build-arg BASE_IMAGE="$digest" -f distribution/Dockerfile -t ocrun-native-image . 2>&1 | tee .native-results/install.txt
fi
docker run -d --name ocrun-native-test --hostname NATIVE-CLOUD --cpus=2 --memory=3g --pids-limit=512 \
    -e container=docker --privileged --cgroupns=private \
    --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
    --tmpfs /run --tmpfs /run/lock --tmpfs /tmp \
    -v "$PWD:/src:ro" -v "$PWD/.native-results:/results" \
    ocrun-native-image /bin/bash -c 'mount -o remount,rw /sys/fs/cgroup; exec /sbin/init'
for attempt in $(seq 1 60); do
    if docker exec ocrun-native-test test -d /run/systemd/system; then break; fi
    sleep 1
done
docker exec ocrun-native-test test -d /run/systemd/system
address=$(docker inspect ocrun-native-test --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
docker exec -e GITHUB_ACTIONS=true -e OCRUN_SOURCE_COMMIT="$OCRUN_SOURCE_COMMIT" ocrun-native-test \
    python3 -I -B /src/distribution/cloud_test.py "$address" 2>&1 | tee .native-results/test.txt
