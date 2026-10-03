#!/bin/bash
set -euo pipefail
[[ ${GITHUB_ACTIONS:-} == true ]]
mkdir -p .independent-upgrade-results
docker pull "$BASE_IMAGE"
digest=$(docker image inspect "$BASE_IMAGE" --format '{{index .RepoDigests 0}}')
printf '%s\n' "$digest" > .independent-upgrade-results/image.txt
docker build --progress plain --build-arg BASE_IMAGE="$digest" --build-arg PACKAGE_DIR=upgrade-from \
    -f native/ci/Dockerfile -t bits-independent-upgrade . 2>&1 | tee .independent-upgrade-results/install.txt
docker run -d --name bits-independent-upgrade --hostname BITS-CLOUD --cpus=2 --memory=3g --pids-limit=512 \
    -e container=docker --privileged --cgroupns=private \
    --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
    --tmpfs /run --tmpfs /run/lock --tmpfs /tmp:rw,exec,nosuid,nodev,mode=1777 \
    -v "$PWD:/src:ro" -v "$PWD/native-dist:/updates:ro" -v "$PWD/.independent-upgrade-results:/results" \
    bits-independent-upgrade /bin/bash -c 'mount -o remount,rw /sys/fs/cgroup; exec /sbin/init'
for attempt in $(seq 1 60); do
    if docker exec bits-independent-upgrade test -d /run/systemd/system; then break; fi
    sleep 1
done
docker exec bits-independent-upgrade test -d /run/systemd/system
address=$(docker inspect bits-independent-upgrade --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
docker exec -e GITHUB_ACTIONS=true bits-independent-upgrade python3 -I -B /src/native/ci/upgrade.py "$address" "$KIND" \
    2>&1 | tee .independent-upgrade-results/test.txt
