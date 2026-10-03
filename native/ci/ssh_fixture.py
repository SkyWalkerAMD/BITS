"""Start a disposable OpenSSH server for real browser SSH/SFTP acceptance."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

assert os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform == "linux"
assert Path("/src/native/ci/acceptance.py").is_file() and Path("/run/systemd/system").is_dir()
subprocess.check_call(["apt-get", "update"], stdout=subprocess.DEVNULL)
subprocess.check_call(["apt-get", "install", "-y", "--no-install-recommends", "openssh-server"],
                      env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"), stdout=subprocess.DEVNULL)
root = Path("/run/bits-ssh-ci")
root.mkdir(mode=0o700)
Path("/run/sshd").mkdir(exist_ok=True)
username, password = "bits-ssh-ci", secrets.token_hex(24)
subprocess.check_call(["useradd", "-m", "-s", "/bin/bash", username])
subprocess.run(["chpasswd"], input=(username + ":" + password + "\n").encode(), check=True)
subprocess.check_call(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / "host")])
(root / "sshd_config").write_text("\n".join([
    "Port 2222", "ListenAddress 0.0.0.0", "HostKey " + str(root / "host"),
    "PidFile " + str(root / "pid"), "PasswordAuthentication yes", "UsePAM no",
    "PermitRootLogin no", "AllowUsers " + username, "Subsystem sftp internal-sftp",
    "PrintMotd no", "LogLevel ERROR", ""]))
subprocess.check_call(["/usr/sbin/sshd", "-f", str(root / "sshd_config")])
fixture = root / "login.json"
fixture.write_text(json.dumps({"username": username, "password": password, "port": 2222}))
fixture.chmod(0o600)
print("Disposable SSH service ready")
