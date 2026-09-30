#!/bin/bash
# Only on the authorized GitHub-hosted Linux runner. No production endpoints.
set -euo pipefail
[[ ${GITHUB_ACTIONS:-} == true ]] || exit 2
profile=${1:?VM profile required}
root=$PWD
mkdir -p .vm-results .vm-private
chmod 700 .vm-private
sudo apt-get update -qq
sudo apt-get install -y -qq qemu-system-x86 qemu-utils cloud-image-utils ovmf
[[ -c /dev/kvm ]] || { echo 'KVM unavailable on this cloud runner' >&2; exit 1; }
case "$profile" in
    rocky8|rocky10)
        major=${profile#rocky}
        image="Rocky-$major-GenericCloud-Base.latest.x86_64.qcow2"
        base="https://dl.rockylinux.org/pub/rocky/$major/images/x86_64"
        curl -fL --retry 3 --connect-timeout 15 --max-time 600 "$base/$image" -o ".vm-private/$image"
        curl -fL --retry 3 --max-time 60 "$base/$image.CHECKSUM" -o .vm-private/CHECKSUM
        (cd .vm-private && sha256sum -c CHECKSUM) | tee .vm-results/image-verification.txt
        ;;
    ubuntu26)
        image=resolute-server-cloudimg-amd64.img
        base=https://cloud-images.ubuntu.com/resolute/current
        curl -fL --retry 3 --connect-timeout 15 --max-time 600 "$base/$image" -o ".vm-private/$image"
        curl -fL --retry 3 --max-time 60 "$base/SHA256SUMS" -o .vm-private/CHECKSUM
        (cd .vm-private && awk -v name="$image" '$2==name || $2=="*"name {print $1 "  " name}' CHECKSUM | sha256sum -c -) | tee .vm-results/image-verification.txt
        ;;
    *) exit 2 ;;
esac
printf '%s/%s\n' "$base" "$image" > .vm-results/image-source.txt
cp .vm-private/CHECKSUM .vm-results/image-checksums.txt
ssh-keygen -q -t ed25519 -N '' -f .vm-private/key
cat > .vm-private/user-data <<EOF
#cloud-config
disable_root: false
ssh_pwauth: false
users:
  - name: root
    ssh_authorized_keys:
      - $(cat .vm-private/key.pub)
EOF
printf 'instance-id: ocrun-cloud-check\nlocal-hostname: ocrun-cloud-check\n' > .vm-private/meta-data
cloud-localds .vm-private/seed.img .vm-private/user-data .vm-private/meta-data
qemu-img create -f qcow2 -F qcow2 -b "$root/.vm-private/$image" .vm-private/guest.qcow2 24G
firmware=()
if [[ $profile != rocky8 ]]; then
    cp /usr/share/OVMF/OVMF_VARS.fd .vm-private/vars.fd
    firmware=(-drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd -drive "if=pflash,format=raw,file=$root/.vm-private/vars.fd")
fi
sudo qemu-system-x86_64 -enable-kvm -cpu host -m 4096 -smp 2 -display none \
    "${firmware[@]}" -drive "file=$root/.vm-private/guest.qcow2,if=virtio,format=qcow2" \
    -drive "file=$root/.vm-private/seed.img,if=virtio,format=raw" \
    -netdev user,id=net0,hostfwd=tcp:127.0.0.1:2222-:22,hostfwd=tcp:127.0.0.1:8088-:80 \
    -device virtio-net-pci,netdev=net0 -serial "file:$root/.vm-results/console.txt" \
    -daemonize -pidfile "$root/.vm-private/qemu.pid"
ssh_args=(-i .vm-private/key -p 2222 -o BatchMode=yes -o ConnectTimeout=3 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=.vm-private/known_hosts)
vm() { ssh "${ssh_args[@]}" root@127.0.0.1 "$@"; }
cleanup() {
    vm 'mkdir -p /results; journalctl --no-pager -u ocrun-server-db -u ocrun-server-http -u ocrun-server-rsync -u ocrun-server-firewall > /results/journal.txt; ausearch -m AVC,USER_AVC -ts boot > /results/avc.txt 2>&1 || true' || true
    vm 'test ! -f /var/lib/ocrun-server-install/failure-diagnostics.txt || cp /var/lib/ocrun-server-install/failure-diagnostics.txt /results/failure-diagnostics.txt' || true
    vm 'tar -C /results -czf - .' | tar --no-same-owner -xzf - -C .vm-results/ || true
    if [[ -s .vm-private/qemu.pid ]]; then sudo kill -TERM "$(sudo cat .vm-private/qemu.pid)" || true; fi
    sudo chown -R "$(id -u):$(id -g)" .vm-results
    tail -n 50 .vm-results/journal.txt .vm-results/avc.txt 2>/dev/null || true
    cat .vm-results/failure-diagnostics.txt 2>/dev/null || true
}
trap cleanup EXIT
for attempt in $(seq 1 240); do
    if vm 'test -f /var/lib/cloud/instance/boot-finished' >/dev/null 2>&1; then break; fi
    sleep 2
done
vm 'test -f /var/lib/cloud/instance/boot-finished; mkdir -p /src /fixture /results'
tar --owner=0 --group=0 -czf .vm-private/source.tar.gz server_deploy finish_addon sckocp_api ci/server_deploy_test.py ci/server_image.sh
scp -i .vm-private/key -P 2222 -o BatchMode=yes -o UserKnownHostsFile=.vm-private/known_hosts .vm-private/source.tar.gz server-dist/ocrun-server-0.1.3.tar.gz root@127.0.0.1:/root/
vm 'tar -xzf /root/source.tar.gz -C /src; tar -xzf /root/ocrun-server-0.1.3.tar.gz -C /root; cp /src/server_deploy/dependencies.sh /fixture/dependencies.sh'
vm 'GITHUB_ACTIONS=true OCRUN_CLOUD_VM=1 bash /src/ci/server_image.sh' 2>&1 | tee .vm-results/packages.txt
if [[ ${OCRUN_SELINUX_TRACE:-false} == true ]]; then
    # Disposable VM diagnostics only: expose suppressed denials without granting
    # access, changing enforcing mode, or shipping a production policy change.
    vm 'systemctl start auditd; semodule -DB; cat /proc/cmdline; getenforce' | tee .vm-results/selinux-trace.txt
fi
vm 'GITHUB_ACTIONS=true OCRUN_CLOUD_VM=1 python3 -I -B /src/ci/server_deploy_test.py 10.0.2.15' 2>&1 | tee .vm-results/services.txt
if curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:8088/config/ocrun-version.txt >/dev/null; then
    echo 'VM allowed an out-of-CIDR request' >&2; exit 1
fi
before=$(vm cat /proc/sys/kernel/random/boot_id)
vm systemctl reboot || true
sleep 10
for attempt in $(seq 1 120); do
    after=$(vm cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)
    if [[ -n $after && $after != "$before" ]]; then break; fi
    sleep 2
done
[[ -n $after && $after != "$before" ]]
for attempt in $(seq 1 30); do
    if vm /usr/local/bin/ocrun-server check > .vm-results/reboot-check.json; then break; fi
    sleep 2
done
vm /usr/local/bin/ocrun-server check > .vm-results/reboot-check.json
vm /usr/local/bin/ocrun-server task status --node CLOUD-NODE > .vm-results/reboot-task.json
printf 'before=%s\nafter=%s\n' "$before" "$after" > .vm-results/boot-ids.txt
