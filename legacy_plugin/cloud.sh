#!/bin/bash
set -euo pipefail
[[ ${GITHUB_ACTIONS:-} == true ]]
mkdir -p .legacy-results
docker pull "$BASE_IMAGE"
digest=$(docker image inspect "$BASE_IMAGE" --format '{{index .RepoDigests 0}}')
printf '%s\n' "$digest" > .legacy-results/image.txt
if [[ $BASE_IMAGE == debian:11 ]]; then
    python3 workload_suite/debian11_ci.py legacy ocrun-legacy-test legacy-dist 2>&1 | tee .legacy-results/install.txt
    cp workload-dist/debian11-dependency-receipt.json .legacy-results/
else
    docker build --build-arg BASE_IMAGE="$digest" -f legacy_plugin/Dockerfile -t ocrun-legacy-test . 2>&1 | tee .legacy-results/install.txt
fi
docker run --rm --network none --hostname LEGACY-CLOUD --cpus=2 --memory=3g --pids-limit=512 \
    -e GITHUB_ACTIONS=true -e GITHUB_SHA -e KIND \
    -v "$PWD:/src:ro" -v "$PWD/.legacy-results:/results" \
    ocrun-legacy-test bash -c 'py=/usr/libexec/platform-python; [ -x "$py" ] || py=/usr/bin/python3; exec "$py" -I -B /src/legacy_plugin/cloud_test.py' \
    2>&1 | tee .legacy-results/test.txt
if [[ $BASE_IMAGE == rockylinux/rockylinux:8 ]]; then
    mkdir -p .legacy-results/field-upgrade
    docker run --rm --network none --hostname LEGACY-CLOUD --cpus=2 --memory=3g --pids-limit=512 \
        -e GITHUB_ACTIONS=true -e GITHUB_SHA -e KIND \
        -v "$PWD:/src:ro" -v "$PWD/.legacy-results/field-upgrade:/results" \
        ocrun-legacy-test /usr/libexec/platform-python -I -B /src/legacy_plugin/cloud_test.py field-upgrade \
        2>&1 | tee .legacy-results/field-upgrade/test.txt
fi
