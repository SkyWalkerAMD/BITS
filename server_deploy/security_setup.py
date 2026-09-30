"""Scoped firewall and SELinux setup; never disable either security mechanism."""
import json
from pathlib import Path
import re
import shutil

from . import safe


def runtime_prefix(tables):
    """EL8 policy uses /var/run while newer policies canonicalize it to /run."""
    for table in tables:  # local substitution takes precedence over distribution
        for raw in table.splitlines():
            columns = raw.split('#', 1)[0].split()
            if len(columns) == 2 and columns[0] == '/run':
                if columns[1] not in ('/run', '/var/run'):
                    raise ValueError('Custom SELinux /run equivalence requires explicit review')
                return columns[1]
    return '/run'


def service_contexts():
    """An active service must actually enter its confined distribution domain."""
    from .install import command
    if not shutil.which('selinuxenabled') or command(['selinuxenabled'], check=False).returncode != 0:
        return {}
    contexts = {}
    for service, expected in (('ocrun-server-db', 'redis_t'), ('ocrun-server-http', 'httpd_t'),
                              ('ocrun-server-rsync', 'rsync_t')):
        pid = command(['systemctl', 'show', '--property=MainPID', '--value', service]).stdout.decode().strip()
        if not pid.isdigit() or int(pid) == 0:
            raise ValueError('No live process for ' + service)
        process = Path('/proc') / pid
        context = (process / 'attr/current').read_text().strip().rstrip('\0')
        fields = context.split(':')
        status = (process / 'status').read_text()
        if len(fields) < 3 or fields[2] != expected:
            raise ValueError('Service SELinux domain differs: {} expected {}, got {}'.format(service, expected, context))
        if not re.search(r'^NoNewPrivs:\s+1\s*$', status, re.M):
            raise ValueError('Service privilege guard is not active: ' + service)
        contexts[service] = context
    return contexts


def configure(config, journal, persist):
    from .install import command
    security = journal.setdefault("security", {"firewalld": [], "ufw": [], "selinux": "not_enabled"})
    if shutil.which("firewall-cmd") and command(["systemctl", "is-active", "--quiet", "firewalld"], check=False).returncode == 0:
        addresses = json.loads(command(["ip", "-j", "address", "show"]).stdout.decode())
        interfaces = [item["ifname"] for item in addresses if any(a.get("local") == config["address"] for a in item["addr_info"])]
        zone = command(["firewall-cmd", "--get-zone-of-interface=" + interfaces[0]], check=False).stdout.decode().strip() if interfaces else ""
        if not zone or zone == "no zone":
            zone = command(["firewall-cmd", "--get-default-zone"]).stdout.decode().strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", zone):
            raise ValueError("Invalid active firewalld zone")
        for port in (80, 873, 6379):
            rule = 'rule family="ipv4" source address="{}" destination address="{}" port port="{}" protocol="tcp" accept'.format(config["network"], config["address"], port)
            for permanent in (False, True):
                base = ["firewall-cmd", "--zone=" + zone] + (["--permanent"] if permanent else [])
                if command(base + ["--query-rich-rule=" + rule], check=False).returncode != 0:
                    entry = {"zone": zone, "permanent": permanent, "rule": rule}
                    security["firewalld"].append(entry)
                    persist()
                    command(base + ["--add-rich-rule=" + rule])
    if shutil.which("ufw"):
        status = command(["ufw", "status"], check=False).stdout.decode()
        if "Status: active" in status:
            if "ocrun-server" in status:
                raise ValueError("Existing unowned OCRUN UFW rule; inspect it before installation")
            for port in (80, 873, 6379):
                args = ["allow", "proto", "tcp", "from", config["network"], "to", config["address"], "port", str(port), "comment", "ocrun-server"]
                security["ufw"].append(args)
                persist()
                command(["ufw"] + args)
    if not shutil.which("selinuxenabled") or command(["selinuxenabled"], check=False).returncode != 0:
        persist()
        return
    # A dedicated result type avoids enabling global anonymous-write booleans.
    # Policy/labels remain with retained data on detach; no unrelated policy is removed.
    # EL8's policy can silently retain init_t when NoNewPrivileges prevents
    # entry into redis_t/httpd_t. Permit only these existing daemon transitions;
    # keep NoNewPrivileges and the distribution file/network confinement intact.
    # This is the narrow process2 permission described by init_nnp_daemon_domain,
    # not write access for init_t or a global SELinux boolean.
    cil = ("(allow init_t redis_t (process2 (nnp_transition)))\n"
           "(allow init_t httpd_t (process2 (nnp_transition)))\n"
           "(type ocrun_server_results_t)\n"
           "(typeattributeset file_type (ocrun_server_results_t))\n"
           "(allow rsync_t ocrun_server_results_t (dir (add_name create getattr ioctl lock open read remove_name rename rmdir search setattr write)))\n"
           "(allow rsync_t ocrun_server_results_t (file (append create getattr ioctl lock map open read rename setattr unlink write)))\n"
           "(allow rsync_t ocrun_server_results_t (lnk_file (create getattr read rename setattr unlink)))\n")
    policy = Path("/var/lib/ocrun-server-install/ocrun_server.cil")
    safe.write(policy, cil.encode())
    command(["semodule", "-i", str(policy)], timeout=120)
    security["selinux"] = "policy_installed"
    persist()

    def context(reference):
        value = command(["matchpathcon", "-n", reference]).stdout.decode().strip().split(":")
        if len(value) < 3 or not re.fullmatch(r"[A-Za-z0-9_]+", value[2]) or value[2] in ("etc_t", "default_t", "var_t"):
            raise ValueError("No service SELinux reference label for " + reference)
        return value[2]

    database = config["platform"]["database"]
    reference = "/etc/{0}/{0}.conf".format(database)
    if not Path(reference).exists():
        reference = "/etc/" + database + ".conf"
    # Inspect the active policy instead of guessing from the distribution name.
    # Existing equivalences are never changed or globally relaxed.
    import selinux
    substitutions = []
    for filename in (selinux.selinux_file_context_subs_path(), selinux.selinux_file_context_subs_dist_path()):
        path = Path(filename)
        if path.exists() or path.is_symlink():
            substitutions.append(safe.read(path).decode('utf-8'))
    runtime = runtime_prefix(substitutions)
    labels = {
        "/etc/ocrun-server/database\\.conf": context(reference),
        "/etc/ocrun-server/rsyncd\\.conf": context("/etc/rsyncd.conf"),
        "/etc/ocrun-server/nginx\\.conf": context("/etc/nginx/nginx.conf"),
        runtime + "/ocrun-server-http(/.*)?": context("/run/nginx.pid"),
        # The distribution policy labels rsyncd.lock, not rsyncd.pid.
        runtime + "/ocrun-server-rsync(/.*)?": context("/run/rsyncd.lock"),
        "/srv/ocrun/public(/.*)?": "public_content_t",
        "/srv/ocrun/logs(/.*)?": "ocrun_server_results_t",
    }
    existing = command(["semanage", "fcontext", "-l", "-C"], timeout=120).stdout.decode()
    for path, label in labels.items():
        lines = [line for line in existing.splitlines() if line.split() and line.split()[0] == path]
        if lines:
            if not all(":" + label + ":" in line for line in lines):
                raise ValueError("Existing SELinux rule differs; preserved: " + path)
        else:
            command(["semanage", "fcontext", "-a", "-t", label, path], timeout=120)
    command(["restorecon", "-RF", "/etc/ocrun-server", "/srv/ocrun", config["database_dir"],
             "/run/ocrun-server-http", "/run/ocrun-server-rsync"], timeout=120)
    security["selinux"] = "configured_without_disabling"
    persist()


def rollback(journal):
    from .install import command
    security = journal.get("security", {})
    for entry in reversed(security.get("firewalld", [])):
        base = ["firewall-cmd", "--zone=" + entry["zone"]] + (["--permanent"] if entry["permanent"] else [])
        command(base + ["--remove-rich-rule=" + entry["rule"]], check=False)
    for args in reversed(security.get("ufw", [])):
        command(["ufw", "--force", "delete"] + args, check=False)
