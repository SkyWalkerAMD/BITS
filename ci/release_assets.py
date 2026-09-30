"""Select all reviewed deployable attachments from an already verified release.

This does not build packages or change their bytes. Research and arbitrary
directory globs are deliberately not publication inputs.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def inventory(root):
    entries = {}
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  ([^\r\n]+)', line)
        if not match:
            raise ValueError('Invalid checksum entry')
        digest, name = match.groups()
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or name in entries:
            raise ValueError('Unsafe or duplicate checksum entry')
        source = root / path
        if source.is_symlink() or not source.is_file() or sha(source) != digest:
            raise ValueError('Unverified release member: ' + name)
        entries[name] = digest
    return entries


def selections(root):
    meta = json.loads((root / 'RELEASE.json').read_text())
    checked = inventory(root)
    names = ['README.md', 'RELEASE.json', 'SOURCES.json', 'ACCEPTANCE-REPORT.md',
             'CONTROL-MANUAL.md', 'WORKLOADS-MANUAL.md', 'SERVER-MANUAL.md',
             'SCKOCP-API.md', 'OPTIMIZATION.md',
             'example/report-preview.html', 'example/report-print-preview.pdf']
    source_names = [name for name in ('sources/ocrun-source.tar.gz', 'sources/bits-source.tar.gz') if name in checked]
    if len(source_names) != 1:
        raise ValueError('Expected exactly one reviewed customer source archive')
    names += source_names
    for role in ('node-rpm', 'node-deb', 'center-rpm', 'center-deb'):
        entry = meta['packages'][role]
        name = entry['file']
        if Path(name).name != name or checked.get(name) != entry['sha256']:
            raise ValueError('Package does not match release metadata: ' + role)
        names.append(name)
    patterns = (
        r'sckocp-api-[0-9]+\.[0-9]+\.[0-9]+\.run',
        r'mon-sensors-plugin-[0-9]+\.[0-9]+\.[0-9]+\.run',
        r'mon-sensors-finish-[0-9]+\.[0-9]+\.[0-9]+\.run',
        r'mon-sensors-control-[0-9]+\.[0-9]+\.[0-9]+\.run',
        r'mon-sensors-report-py36-[0-9]+\.[0-9]+\.[0-9]+\.run',
        r'(?:ocrun|bits-o)-workloads-[0-9]+\.[0-9]+\.[0-9]+-[0-9]+\.el8\.x86_64\.rpm',
        r'(?:ocrun|bits-o)-workloads_[0-9]+\.[0-9]+\.[0-9]+-[0-9]+_amd64\.deb',
    )
    standalone = [p for p in (root / 'standalone').iterdir() if p.is_file()]
    selected = []
    for pattern in patterns:
        matches = [p for p in standalone if re.fullmatch(pattern, p.name)]
        if len(matches) != 1:
            raise ValueError('Expected exactly one component: ' + pattern)
        selected += matches
    if set(standalone) != set(selected):
        raise ValueError('Review new standalone components before publication')
    names += [p.relative_to(root).as_posix() for p in selected]
    if len({Path(n).name for n in names}) != len(names):
        raise ValueError('Duplicate attachment basename')
    for name in names:
        if name not in checked:
            raise ValueError('Attachment missing from verified manifest: ' + name)
    return [root / name for name in names]


def prepare(root, output):
    root, output = Path(root), Path(output)
    chosen = selections(root)
    archive = root.parent / (root.name + '.tar.gz')
    expected = (Path(str(archive) + '.sha256')).read_text().split()
    if expected != [sha(archive), archive.name]:
        raise ValueError('Distribution archive checksum mismatch')
    chosen += [archive, Path(str(archive) + '.sha256')]
    output.mkdir(exist_ok=False)
    for source in chosen:
        shutil.copyfile(str(source), str(output / source.name))
    entries = {p.name: {'sha256': sha(p), 'bytes': p.stat().st_size}
               for p in sorted(output.iterdir())}
    (output / 'SHA256SUMS').write_text(''.join(v['sha256'] + '  ' + k + '\n'
                                             for k, v in entries.items()))
    entries['SHA256SUMS'] = {'sha256': sha(output / 'SHA256SUMS'),
                           'bytes': (output / 'SHA256SUMS').stat().st_size}
    return entries


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('root')
    parser.add_argument('output')
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.output), indent=2))
