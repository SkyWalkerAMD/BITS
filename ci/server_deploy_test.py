"""Real systemd/database/rsync/nginx tests inside a disposable cloud OS."""
import ast
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

if os.environ.get("GITHUB_ACTIONS") != "true" or os.geteuid() != 0:
    raise SystemExit("Disposable cloud root environment required")
source = Path("/root/ocrun-server-0.1.3")
sys.path.insert(0, str(source))
from bits_core.center import install, platforms, render, safe, tasks
from bits_core.center.wire import Redis, ProtocolError

checks = []


def run(*argv, **kwargs):
    result = subprocess.run(list(argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=kwargs.get("timeout", 120))
    if kwargs.get("success", True) and result.returncode:
        raise AssertionError("{} failed: {}".format(argv[0], result.stderr.decode("utf-8", "replace")[-2000:]))
    return result


def record(name):
    checks.append(name)
    print("PASS " + name, flush=True)


def main():
    address = sys.argv[1]
    network = address + "/32"
    platform = platforms.profile(platforms.read_release())
    config = render.configuration(address, network, platform)
    manifest = install.package(source)
    binary = ["bash", str(source / "bits_core/center/install.sh"), "--address", address, "--network", network, "--skip-deps"]
    run("nft", "add", "table", "inet", "ocrun_fixture")
    run(*(binary + ["--check"]))
    assert not Path("/etc/ocrun-server/server.json").exists()
    assert not install.CURRENT.exists()
    record("read-only fresh install preflight does not activate or write configuration")
    outputs = render.files(config, str(Path(sys.executable).resolve()))
    outputs["/etc/ocrun-server/nginx.conf"] = ("this_is_invalid_nginx;\n", 0o644)
    try:
        install.apply(config, source, outputs, manifest)
        raise AssertionError("Invalid nginx config installed")
    except ValueError as error:
        assert "nginx failed" in str(error), str(error)
    assert not install.CURRENT.exists()
    assert not Path("/etc/ocrun-server/server.json").exists()
    prepared_password = safe.load(install.STATE / "prepared.json")["config"]["password"]
    run("nft", "list", "table", "inet", "ocrun_fixture")
    record("failed activation rolls back owned files and retains credentials/unrelated firewall")
    run(*(binary + ["--apply"]))
    config = safe.load("/etc/ocrun-server/server.json")
    assert config["password"] == prepared_password
    redis = Redis(password=prepared_password)
    result = json.loads(run("/usr/local/bin/ocrun-server", "check").stdout.decode())
    assert result["status"] == "ok"
    record("corrected retry starts real systemd database/nginx/rsync with original credentials")
    before = safe.read(install.MANIFEST)
    run(*(binary + ["--apply"]))
    assert safe.read(install.MANIFEST) == before
    record("repeated install preserves managed bytes and credentials")
    run("su", "-s", "/bin/sh", "ocuser", "-c", "/usr/local/bin/ocrun-server task status --node CLOUD-NODE")
    record("ocuser can operate task management without root")
    rejected = run("/usr/local/bin/ocrun-server", "task", "add", "--node", "CLOUD-NODE", "--id", "TYPO", "--task", "strss=60", success=False)
    assert rejected.returncode != 0 and redis.call("GET", "CLOUD-NODE") is None
    record("misspelled task rejected without writing host mapping or taking tasks")
    queued = tasks.add(redis, "CLOUD-NODE", "CLOUD-BATCH", "20260927-TEST", ["stress=60", "stress-ng=60"])
    db = queued["db"]
    assert redis.call("GET", "CLOUD-NODE") == str(db)
    assert redis.call("GET", "IDS", db=db) == "CLOUD-BATCH"
    assert redis.call("LRANGE", "TASKS", 0, -1, db=db) == ["stress", "stress-ng"]
    record("task management uses unchanged DB0/IDS/DATES/TASKS/duration layout")
    # The exact Lua scripts used by the field-tested finalizer, not a rewritten
    # new-architecture queue. Only the workload itself is simulated here.
    tree = ast.parse(Path("/src/bits_core/batch/queue.py").read_text())
    lua = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ("SNAPSHOT", "CLAIM", "UPDATE")}
    try:
        Redis(host=address).call("PING")
        raise AssertionError("Unauthenticated remote database request succeeded")
    except ProtocolError:
        pass
    record("protected mode remains enabled and remote database access requires credentials")
    original = Redis(host=address, password=config["node_password"], username=None)
    snapshot = json.loads(original.call("EVAL", lua["SNAPSHOT"], 0, db=db))
    assert len(snapshot["tasks"]) == 2
    assert original.call("EVAL", lua["CLAIM"], 0, snapshot["id"], snapshot["time"], json.dumps(snapshot["tasks"]), "fixture:0", db=db) == "claimed"
    try:
        tasks.delete(redis, "CLOUD-NODE", "CLOUD-BATCH")
        raise AssertionError("Active batch deleted")
    except ProtocolError:
        pass
    assert original.call("EVAL", lua["UPDATE"], 0, snapshot["id"], snapshot["time"], "fixture:0", "delivered", "", db=db) == "updated"
    record("original finalizer Lua interoperates and running-batch deletion is refused")
    try:
        original.call("CONFIG", "GET", "*")
        raise AssertionError("Anonymous node obtained administrative configuration")
    except ProtocolError:
        pass
    record("authenticated legacy node role cannot use CONFIG admin command")
    # Actual node client implementation with its isolated child environment;
    # a synthetic oc.env exercises connection changes without running workloads.
    from bits_core.center import connection, node_connect
    fixture = Path('/root/node-connect-fixture')
    fixture.mkdir()
    env_before = b'LOGSVR1=192.0.2.1\nLOGSVR2=192.0.2.2\nRDBSVR1=192.0.2.3\nRDBSVR2=192.0.2.4\n'
    (fixture / 'oc.env').write_bytes(env_before)
    (fixture / 'oc.env').chmod(0o755)
    os.chown(str(fixture / 'oc.env'), 201, 200)
    safe.save(fixture / '.mon-sensors-finish-install.json', {'version': '0.2.2'})
    private = Path('/root/fixture-node.json')
    exported = run('/usr/local/bin/ocrun-server', 'node-config', '--node', socket.gethostname(), '--output', str(private))
    assert config['node_password'].encode() not in exported.stdout
    node_connect.ORIGINAL_ENV = safe.digest(env_before)  # test fixture only
    previous_args = sys.argv
    for action in ('--check', '--apply', '--apply'):
        sys.argv = ['node-connect', '--app', str(fixture), '--config', str(private), action]
        node_connect.main()
        if action == '--check':
            assert not Path('/etc/ocrun-node/connection.json').exists()
    sys.argv = previous_args
    sys.path[:0] = ['/src/finish_addon', '/src/sckocp_api']
    import importlib.util
    spec = importlib.util.spec_from_file_location('authenticated_original_queue', '/src/bits_core/batch/queue.py')
    node_queue = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(node_queue)
    current_node = socket.gethostname()
    tasks.add(redis, current_node, 'AUTH-CLIENT', 'cloud', ['stress=60'])
    client = node_queue.Queue(address, current_node)
    assert client.snapshot()['id'] == 'AUTH-CLIENT'
    try:
        node_queue.Queue('192.0.2.99', current_node)
        raise AssertionError('Node credentials accepted for an unrelated endpoint')
    except ValueError:
        pass
    before_mode = private.stat().st_mode & 0o777
    assert before_mode == 0o600
    sys.argv = ['node-connect', '--app', str(fixture), '--rollback']
    node_connect.main()
    sys.argv = previous_args
    assert (fixture / 'oc.env').read_bytes() == env_before
    assert (fixture / 'oc.env').stat().st_uid == 201
    assert not Path('/etc/ocrun-node/connection.json').exists()
    record('private node connection preflight/import/reimport/exact rollback and actual authenticated node Queue')
    # Interrupt after journal creation and after the first private file. The
    # rollback must restore both content and adopted ownership without a task.
    original_write = node_connect.safe.write
    def interrupted_write(path, data, mode=0o600):
        if Path(path).name == 'connection.env':
            raise OSError('simulated connection interruption')
        return original_write(path, data, mode)
    node_connect.safe.write = interrupted_write
    sys.argv = ['node-connect', '--app', str(fixture), '--config', str(private), '--apply']
    try:
        node_connect.main()
        raise AssertionError('Connection interruption was not injected')
    except OSError as error:
        assert 'simulated' in str(error)
    finally:
        node_connect.safe.write = original_write
        sys.argv = previous_args
    assert (fixture / 'oc.env').read_bytes() == env_before
    sys.argv = ['node-connect', '--app', str(fixture), '--rollback']
    node_connect.main()
    sys.argv = previous_args
    assert (fixture / 'oc.env').stat().st_uid == 201
    assert not Path('/etc/ocrun-node/connection.json').exists()
    record('interrupted private connection import rolls back without changing original ownership or starting tasks')
    from bits_core.center.publish import publish
    installer = Path('/root/mon-sensors-finish-0.2.2.run')
    installer.write_bytes(b'#!/bin/sh\nexit 99\n')
    installer.chmod(0o600)
    checksum = safe.digest(installer.read_bytes())
    published = publish(config, str(installer), checksum)
    assert publish(config, str(installer), checksum)['url'] == published['url']
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    assert opener.open(published['url'], timeout=5).read() == installer.read_bytes()
    try:
        publish(config, str(private), safe.digest(private.read_bytes()))
        raise AssertionError('Private connection file was published')
    except ValueError:
        pass
    record('selected installer publishes by exact hash and private connection files are refused')
    run("systemctl", "restart", "ocrun-server-db")
    for unused in range(50):
        try:
            assert redis.call("GET", "IDS", db=db) == "CLOUD-BATCH"
            break
        except (OSError, AssertionError):
            time.sleep(.2)
    else:
        raise AssertionError("Database restart did not recover batch")
    record("real database restart retains mapping, batch and remaining task")
    with tempfile.TemporaryDirectory(prefix="server-fixture-", dir="/root") as temporary:
        directory = Path(temporary)
        base = "CLOUD-NODE_SERIAL_CLOUD-BATCH_20260927-TEST"
        # Transport fixtures only; Excel generation and native sensors belong
        # to the separately verified node components.
        for suffix in ("mon", "mon.sckocp.jsonl", "xlsx", "finish.json"):
            path = directory / (base + "." + suffix)
            path.write_bytes(("synthetic-transport-" + suffix + "\n").encode())
            path.chmod(0o600)
        expected = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
        run("rsync", "-a", "--contimeout=5", "--timeout=10", str(directory) + "/", address + "::logs/CLOUD-NODE_SERIAL/")
        downloaded = directory / "downloaded"
        downloaded.mkdir()
        run("rsync", "-rt", "--contimeout=5", "--timeout=10", address + "::logs/CLOUD-NODE_SERIAL/", str(downloaded) + "/")
        for name, checksum in expected.items():
            for prefix in (downloaded, Path("/data/cds/result/CLOUD-NODE_SERIAL")):
                path = prefix / name
                assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum
                assert path.stat().st_mode & 0o777 == 0o600
            run("su", "-s", "/bin/sh", "ocuser", "-c", "sha256sum /data/cds/result/CLOUD-NODE_SERIAL/" + name)
        record("four result files upload/download/hash-match and remain readable by ocuser with mode 0600")
        retained = expected
    nginx = Path("/etc/ocrun-server/nginx.conf")
    saved = nginx.read_bytes()
    nginx.write_bytes(saved + b"# local customization\n")
    assert run(*(binary + ["--apply"]), success=False).returncode != 0
    assert nginx.read_bytes().endswith(b"# local customization\n")
    nginx.write_bytes(saved)
    record("modified managed file is preserved and rejected instead of overwritten")
    run(*(binary + ["--rollback"]))
    assert not install.CURRENT.exists()
    assert not install.MANIFEST.exists()
    for name, checksum in retained.items():
        assert hashlib.sha256(Path("/srv/ocrun/logs/CLOUD-NODE_SERIAL", name).read_bytes()).hexdigest() == checksum
    run("nft", "list", "table", "inet", "ocrun_fixture")
    record("rollback detaches only owned services/files while retaining results/database/accounts")
    run(*(binary + ["--apply"]))
    assert safe.load("/etc/ocrun-server/server.json")["password"] == prepared_password
    assert tasks.status(Redis(password=prepared_password), "CLOUD-NODE")["id"] == "CLOUD-BATCH"
    record("reinstall after rollback reuses prepared credentials and persisted task database")
    vm = os.environ.get('OCRUN_CLOUD_VM') == '1'
    enforcing = vm and platform['family'] == 'el' and run('getenforce').stdout.strip() == b'Enforcing'
    if vm and platform['family'] == 'el':
        assert enforcing, 'Native EL VM must keep SELinux enforcing'
    value = {"status": "passed", "checks": checks, "platform": platform, "kernel": os.uname().release,
             "systemd": run("systemctl", "--version").stdout.decode().splitlines()[0],
             "database_version": run(platform["database_binary"], "--version").stdout.decode().strip(),
             "environment": 'disposable KVM with distribution kernel' if vm else "privileged disposable container with real systemd; shared runner kernel",
             "native_hardware": False, "native_selinux_enforcing": enforcing,
             "protocol": "ocrun-legacy-v1"}
    Path("/results/server.json").write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps(value), flush=True)


try:
    main()
except BaseException as error:
    Path("/results/server.json").write_text(json.dumps({"status": "failed", "checks": checks, "error": str(error)}, indent=2) + "\n")
    raise
