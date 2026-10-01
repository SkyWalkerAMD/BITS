"""Export reviewed customer sources, never the complete private repository."""
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile

POLICY = 'distribution/customer-sources.json'
ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ('research/', 'ci/', 'integrations/sckocp', 'drafts/', '.git/', '.codex/', '.cloud-results/',
           'docs/security/SCKOCP-NATIVE-SECURITY.md', 'docs/security/SCKOCP-SERVER-MIGRATION.md')
COMPONENTS = ('native/', 'bits_core/', 'distribution/', 'legacy_plugin/', 'bits_core/collector/', 'sckocp_api/',
              'bits_core/batch/', 'bits_core/reporting/', 'bits_core/results/', 'bits_core/center/',
              'bits_core/workloads/')


def git(repository, *args):
    return subprocess.check_output(['git', '-C', str(repository)] + list(args))


def checked_names(value):
    if not isinstance(value, list) or not value or len(set(value)) != len(value):
        raise ValueError('Customer source list must contain unique explicit filenames')
    for name in value:
        if (not isinstance(name, str) or not name or name.startswith('-') or
                str(PurePosixPath(name)) != name or PurePosixPath(name).is_absolute() or
                '..' in PurePosixPath(name).parts or '\\' in name or ':' in name or
                any(ord(c) < 32 for c in name) or any(c in name for c in '*?[') or
                name.startswith(PRIVATE) or name.endswith(('.pem', '.key', '.p12', '.pyc'))):
            raise ValueError('Forbidden customer source path: ' + str(name))
        if name.startswith('integrations/') and not name.startswith('integrations/mon-sensors/'):
            raise ValueError('Native reference implementations are private')
    return sorted(value)


def export_sources(output, commit, repository=ROOT):
    # The policy and the files both come from the tested commit, not untracked
    # worktree files or a directory glob. Adding source requires list review.
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('A full tested source commit is required')
    policy = json.loads(git(repository, 'show', commit + ':' + POLICY).decode('utf-8'))
    if policy.get('schema') != 'ocrun-customer-sources-v1':
        raise ValueError('Unknown customer source policy')
    names = checked_names(policy['files'])
    tree = {}
    for item in git(repository, 'ls-tree', '-r', '-z', commit).split(b'\0'):
        if item:
            metadata, name = item.split(b'\t', 1)
            mode, kind, unused = metadata.decode('ascii').split()
            tree[name.decode('utf-8')] = (mode, kind)
    for name in names:
        if tree.get(name) not in (('100644', 'blob'), ('100755', 'blob')):
            raise ValueError('Missing or non-regular customer source: ' + name)
    # New production files fail closed until explicitly reviewed. Private
    # reference source is outside these component directories.
    missing = sorted(n for n in tree if n.startswith(COMPONENTS) and
                     n.endswith(('.py', '.sh', '.json', '.in', '.go', '.js', '.css', '.html', 'go.mod', 'go.sum')) and n not in names)
    if missing:
        raise ValueError('Review new component sources before release: ' + ', '.join(missing))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    stamp = int(git(repository, 'show', '-s', '--format=%ct', commit).strip())
    inventory = {}
    # Archive only explicit, already type-checked files. Never follow links or
    # include a directory path. Verify git attributes did not omit anything.
    with tempfile.TemporaryFile() as raw:
        subprocess.run(['git', '-C', str(repository), 'archive', '--format=tar', commit, '--'] + names,
                       stdout=raw, check=True)
        raw.seek(0)
        with tarfile.open(fileobj=raw, mode='r:') as source, output.open('xb') as stream:
            with gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode='w') as target:
                    for member in source:
                        if member.isdir():
                            continue
                        if not member.isfile() or member.name not in names or member.name in inventory:
                            raise ValueError('Unexpected source archive member: ' + member.name)
                        data = source.extractfile(member).read()
                        inventory[member.name] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
                        item = tarfile.TarInfo(member.name)
                        item.size, item.mtime = len(data), stamp
                        item.mode = int(tree[member.name][0], 8) & 0o777
                        target.addfile(item, io.BytesIO(data))
                    if set(inventory) != set(names):
                        raise ValueError('Customer source archive is incomplete')
                    manifest = json.dumps({'schema': 'ocrun-customer-source-manifest-v1',
                        'source_commit': commit, 'policy': POLICY, 'files': inventory}, sort_keys=True, indent=2).encode('utf-8') + b'\n'
                    item = tarfile.TarInfo('SOURCE-MANIFEST.json')
                    item.size, item.mode, item.mtime = len(manifest), 0o644, stamp
                    target.addfile(item, io.BytesIO(manifest))
    return {'source_commit': commit, 'files': len(inventory),
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'policy': POLICY}
