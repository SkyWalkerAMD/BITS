"""Render isolated server configuration; --root supports review without installation."""
import argparse
import hashlib
import ipaddress
import json
import os
import secrets

from .common import identifier, read_json, write_json


def save(root, path, content, mode=0o644):
    target = os.path.join(root, path.lstrip("/"))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    os.chmod(target, mode)
    return target


def render_server(root, address, network):
    address = str(ipaddress.IPv4Address(address))
    subnet = ipaddress.IPv4Network(network, strict=False)
    if ipaddress.IPv4Address(address) not in subnet:
        raise ValueError("Server address must belong to the allowed client network")
    token = secrets.token_hex(32)
    config = {"redis": {"host": "127.0.0.1", "port": 6380, "username": "admin", "password": token},
              "address": address, "network": str(subnet), "data_root": "/srv/ocrun",
              "enrollments": "/etc/ocrun/enrollments"}
    write_json(os.path.join(root, "etc/ocrun/server.json"), config)
    save(root, "/var/lib/ocrun-redis/users.acl",
         "user default off\nuser admin on #{} ~* +@all\n".format(hashlib.sha256(token.encode()).hexdigest()), 0o600)
    save(root, "/etc/ocrun/redis.conf", """bind 127.0.0.1 {address}
port 6380
protected-mode yes
daemonize no
supervised no
dir /var/lib/ocrun-redis
aclfile /var/lib/ocrun-redis/users.acl
appendonly yes
appendfsync everysec
save 900 1
logfile ""
""".format(address=address))
    save(root, "/etc/systemd/system/ocrun-redis.service", """[Unit]
Description=OCRUN task store
After=network.target
[Service]
User=redis
Group=redis
ExecStart=/usr/bin/redis-server /etc/ocrun/redis.conf
Restart=on-failure
UMask=0077
LimitNOFILE=65535
[Install]
WantedBy=multi-user.target
""")
    save(root, "/etc/systemd/system/ocrun-rsync.service", """[Unit]
Description=OCRUN log receiver
After=network.target
[Service]
ExecStart=/usr/bin/rsync --daemon --no-detach --config=/etc/ocrun/rsyncd.conf
Restart=on-failure
[Install]
WantedBy=multi-user.target
""")
    save(root, "/etc/nginx/conf.d/ocrun.conf", """server {{
    listen {address}:8080;
    server_name _;
    root /srv/ocrun/public;
    autoindex off;
    allow {network};
    deny all;
    location / {{ try_files $uri =404; }}
}}
""".format(address=address, network=subnet))
    render_rsync(root, config, [])
    return config


def render_rsync(root, server, enrollments):
    contents = """address = {address}
port = 1873
pid file = /run/ocrun-rsync.pid
lock file = /run/ocrun-rsync.lock
log file = /var/log/ocrun-rsync.log
uid = ocrun-logs
gid = ocrun-logs
use chroot = yes
hosts allow = {network} 127.0.0.1
hosts deny = *
secrets file = /etc/ocrun/rsync.secrets
strict modes = yes
max connections = 32
timeout = 120
""".format(address=server["address"], network=server["network"])
    credentials = []
    for entry in enrollments:
        host = identifier(entry["host_id"])
        contents += """
[logs-{host}]
path = /srv/ocrun/logs/{host}
auth users = {host}
read only = no
write only = yes
list = no
munge symlinks = yes
refuse options = delete delete-excluded remove-source-files copy-links
""".format(host=host)
        credentials.append(host + ":" + entry["upload"]["password"])
    save(root, "/etc/ocrun/rsyncd.conf", contents)
    save(root, "/etc/ocrun/rsync.secrets", "\n".join(credentials) + "\n", 0o600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="Staging directory; never implicitly writes to /")
    parser.add_argument("--address", required=True)
    parser.add_argument("--network", required=True)
    args = parser.parse_args()
    if os.path.exists(os.path.join(args.root, "etc/ocrun/server.json")):
        parser.error("Staging configuration already exists; credentials will not be overwritten")
    render_server(args.root, args.address, args.network)
    print("Server configuration rendered under " + os.path.abspath(args.root))


if __name__ == "__main__":
    main()
