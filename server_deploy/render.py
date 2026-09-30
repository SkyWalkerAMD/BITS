"""Render a fresh, original-protocol center. No existing production configuration."""
import hashlib
import ipaddress
import json
import secrets

from . import VERSION


SERVICES = ("ocrun-server-firewall", "ocrun-server-db", "ocrun-server-rsync", "ocrun-server-http")


def configuration(address, network, platform, password=None):
    address = ipaddress.IPv4Address(address)
    network = ipaddress.IPv4Network(network, strict=False)
    if address.is_unspecified or address.is_multicast or network.prefixlen < 8:
        raise ValueError("Use an explicit server IPv4 address and a bounded management CIDR")
    return {"schema": "ocrun-server-v1", "version": VERSION, "protocol": "ocrun-legacy-v1",
            "address": str(address), "network": str(network), "platform": platform,
            "password": password or secrets.token_hex(32), "databases": 2048,
            "node_password": secrets.token_hex(32), "database_authentication": "required",
            "results": "/srv/ocrun/logs", "public": "/srv/ocrun/public",
            "database_dir": platform["database_home"] + "/ocrun-server",
            "automatic_task_polling": False}


def unit(description, command, user=None, before=None, runtime=None):
    return ("[Unit]\nDescription=" + description + "\nAfter=network.target ocrun-server-firewall.service\n"
            "Requires=ocrun-server-firewall.service\n" + ("Before=" + before + "\n" if before else "") +
            "[Service]\nType=simple\n" + ("User=" + user + "\nGroup=" + user + "\n" if user else "") +
            ("RuntimeDirectory=" + runtime + "\nRuntimeDirectoryMode=0755\n" if runtime else "") +
            "ExecStart=" + command + "\nRestart=on-failure\nRestartSec=3\nUMask=0077\n"
            "NoNewPrivileges=true\nPrivateTmp=true\n[Install]\nWantedBy=multi-user.target\n")


def files(config, python):
    address, network = config["address"], config["network"]
    platform = config["platform"]
    bind = "127.0.0.1" if address == "127.0.0.1" else "127.0.0.1 " + address
    database = ("bind {bind}\nport 6379\nprotected-mode yes\ndaemonize no\nsupervised no\n"
                "databases {count}\ndir {directory}\nappendonly yes\nappendfsync everysec\n"
                "save 900 1\nlogfile \"\"\n"
                # Keep the original keys and Lua protocol, adding authentication
                # for connections to this new server. Existing centers are untouched.
                "user default on #{node_password} ~* +ping +select +get +set +del +exists +llen +lrange +lindex +lpop +rpop +rpush +lpush +lrem +scan +keys +dbsize +info +type +eval +evalsha +script|load\n"
                "user ocrun-admin on #{password} ~* +@all\n").format(
                    bind=bind, count=config["databases"], directory=config["database_dir"],
                    node_password=hashlib.sha256(config["node_password"].encode("ascii")).hexdigest(),
                    password=hashlib.sha256(config["password"].encode("ascii")).hexdigest())
    rsync = ("address = {address}\nport = 873\npid file = /run/ocrun-server-rsync/server.pid\n"
             "lock file = /run/ocrun-server-rsync/server.lock\nuid = ocuser\ngid = ocgroup\n"
             "use chroot = yes\nstrict modes = yes\nmax connections = 64\ntimeout = 120\n"
             "hosts allow = {network} 127.0.0.1\nhosts deny = *\n"
             "[logs]\npath = /srv/ocrun/logs\nread only = no\nlist = yes\n"
             "munge symlinks = yes\nrefuse options = delete delete-excluded remove-source-files copy-links\n"
             "[ocrun]\npath = /srv/ocrun/public/ocrun\nread only = yes\nlist = yes\n").format(**config)
    nginx = ("user {user};\nworker_processes auto;\npid /run/ocrun-server-http/server.pid;\n"
             "error_log stderr;\nevents {{ worker_connections 1024; }}\n"
             "http {{ access_log off;\n"
             "default_type application/octet-stream; server_tokens off;\n"
             "server {{ listen {address}:80; server_name _; root /srv/ocrun/public;\n"
             "autoindex off; allow 127.0.0.1; allow {network}; deny all;\n"
             "location / {{ limit_except GET {{ deny all; }} try_files $uri =404; }}\n}}\n}}\n").format(
                 user="nginx" if platform["family"] == "el" else "www-data", **config)
    sources = "127.0.0.0/8" if ipaddress.IPv4Network(network).network_address in ipaddress.IPv4Network("127.0.0.0/8") else "127.0.0.0/8, " + network
    firewall = ("table inet ocrun_server {{\n chain ingress {{\n"
                "type filter hook input priority -5; policy accept;\n"
                "ip daddr {address} tcp dport {{ 80, 873, 6379 }} ip saddr {{ {sources} }} accept\n"
                "ip daddr {address} tcp dport {{ 80, 873, 6379 }} counter drop\n }}\n}}\n").format(sources=sources, **config)
    firewall_unit = ("[Unit]\nDescription=OCRUN legacy management-network access guard\nAfter=network-pre.target\n"
                     "Before=ocrun-server-db.service ocrun-server-http.service ocrun-server-rsync.service\n"
                     "[Service]\nType=oneshot\nRemainAfterExit=yes\n"
                     "ExecStart=/usr/local/libexec/ocrun-server-guard start\n"
                     "ExecStop=/usr/local/libexec/ocrun-server-guard stop\n[Install]\nWantedBy=multi-user.target\n")
    launcher = "#!/bin/sh\nexec {} -I -B -c 'import runpy,sys;sys.path.insert(0,\"/opt/ocrun-server/current\");runpy.run_module(\"server_deploy.cli\",run_name=\"__main__\")' \"$@\"\n".format(python)
    output = {
        "/etc/ocrun-server/server.json": (json.dumps(config, indent=2, sort_keys=True) + "\n", 0o640),
        "/etc/ocrun-server/database.conf": (database, 0o640),
        "/etc/ocrun-server/rsyncd.conf": (rsync, 0o644),
        "/etc/ocrun-server/nginx.conf": (nginx, 0o644),
        "/etc/ocrun-server/ingress.nft": (firewall, 0o600),
        "/etc/systemd/system/ocrun-server-firewall.service": (firewall_unit, 0o644),
        "/etc/systemd/system/ocrun-server-db.service": (unit("OCRUN original-protocol task database", platform["database_binary"] + " /etc/ocrun-server/database.conf", platform["database_user"]), 0o644),
        "/etc/systemd/system/ocrun-server-rsync.service": (unit("OCRUN original-protocol log receiver", "/usr/bin/rsync --daemon --no-detach --config=/etc/ocrun-server/rsyncd.conf", runtime="ocrun-server-rsync"), 0o644),
        "/etc/systemd/system/ocrun-server-http.service": (unit("OCRUN original-protocol resource distribution", "/usr/sbin/nginx -c /etc/ocrun-server/nginx.conf -g 'daemon off;'", runtime="ocrun-server-http"), 0o644),
        "/usr/local/bin/ocrun-server": (launcher, 0o755),
        "/usr/local/bin/occt": ("#!/bin/sh\nexec /usr/local/bin/ocrun-server menu\n", 0o755),
        "/usr/local/bin/ws": ("#!/bin/sh\nexec /bin/su - ocuser\n", 0o755),
        "/srv/ocrun/public/config/ocrun-version.txt": ("MAIN_VERSION=0.9.24a\nDEV_VERSION=0.9.24a\n", 0o644),
        "/srv/ocrun/public/index.html": ("<!doctype html><meta charset=utf-8><title>OCRUN</title><p>OCRUN resource server. Use occt for task management.</p>\n", 0o644),
    }
    return output
