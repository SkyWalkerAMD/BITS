#!/usr/bin/env python3
"""Explicit inventory, node binding and import of user-owned licensed tools."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).absolute().parent))
import suite


def import_archive(tool, archive, dry_run):
    manifest = suite.check()
    source = manifest['sources'][tool]
    archive = Path(archive)
    # The source is unexecuted data, but root permissions/path integrity still matter.
    actual = suite.metadata(archive)
    if actual['sha256'] != source['sha256']:
        raise ValueError('Expected the pinned original {} archive; SHA-256 differs'.format(tool))
    if tool == 'mlc' and 'mlc/mlc' in manifest['files']:
        return {'status': 'included', 'tool': tool, 'path': str(suite.PREFIX / 'mlc'),
                'source_sha256': actual['sha256'], 'check': dry_run, 'source_modified': False}
    target = suite.EXTERNAL / (tool + '-' + source['version'])
    if target.exists():
        suite.imported(tool)
        return {'status': 'already_imported', 'tool': tool, 'path': str(target)}
    if dry_run:
        return {'status': 'checked', 'tool': tool, 'source_sha256': actual['sha256'], 'destination': str(target)}
    suite.EXTERNAL.mkdir(mode=0o700, exist_ok=True)
    suite.directory(suite.EXTERNAL)
    # Avoid writing a large SPEC tree into a nearly full filesystem.
    required = 4 * 1024 ** 3 if tool == 'cpu2017' else 16 * 1024 ** 2
    if shutil.disk_usage(str(suite.EXTERNAL)).free < required:
        raise ValueError('Insufficient import space')
    stage = Path(tempfile.mkdtemp(prefix='.import-', dir=str(suite.EXTERNAL)))
    files, total = {}, 0
    try:
        with suite.opened(archive) as f, tarfile.open(fileobj=f) as bundle:
            for member in bundle:
                name = member.name
                while name.startswith('./'):
                    name = name[2:]
                if PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts:
                    raise ValueError('Invalid source archive path')
                if tool == 'mlc':
                    if name == 'Linux/mlc':
                        relative = 'mlc'
                    elif name.startswith('Documentation/') or name.endswith('.pdf') or name == 'Linux/redist.txt':
                        relative = 'documentation/' + PurePosixPath(name).name
                    else:
                        continue
                else:
                    if name.rstrip('/') == 'ocrun/cpu2017' and member.isdir():
                        continue
                    if not name.startswith('ocrun/cpu2017/'):
                        continue
                    relative = name[len('ocrun/cpu2017/'):]
                p = PurePosixPath(relative)
                if not p.parts or p.is_absolute() or '..' in p.parts or relative == 'IMPORT.json':
                    raise ValueError('Invalid import path')
                dest = stage.joinpath(*p.parts)
                if member.isdir():
                    dest.mkdir(mode=0o755, parents=True, exist_ok=True)
                    continue
                if member.issym() or member.islnk():
                    linked = (posixpath.normpath(posixpath.join(posixpath.dirname(name), member.linkname))
                              if member.issym() else member.linkname)
                    if tool != 'cpu2017' or not linked.startswith('ocrun/cpu2017/') or '..' in PurePosixPath(linked).parts:
                        raise ValueError('External archive link rejected')
                    # Materialize internal links as private regular files with identical bytes.
                elif not member.isfile():
                    raise ValueError('Unsupported import entry')
                dest.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                h, size = hashlib.sha256(), 0
                with bundle.extractfile(member) as inp, dest.open('xb') as out:
                    for block in iter(lambda: inp.read(1024 * 1024), b''):
                        size += len(block)
                        total += len(block)
                        if total > 8 * 1024 ** 3:
                            raise ValueError('Import exceeds 8 GiB bound')
                        h.update(block)
                        out.write(block)
                dest.chmod(0o755 if member.mode & 0o111 else 0o644)
                files[relative] = {'sha256': h.hexdigest(), 'bytes': size}
        for required in (('mlc',) if tool == 'mlc' else ('shrc', 'bin/runcpu', 'version.txt')):
            if required not in files:
                raise ValueError('Required upstream file missing: ' + required)
        if tool == 'cpu2017' and (stage / 'version.txt').read_text().strip() != '1.0.5':
            raise ValueError('SPEC version differs from original 1.0.5')
        suite.save(stage / 'IMPORT.json', {'schema': 'ocrun-import-v1', 'tool': tool,
                   'source_sha256': actual['sha256'], 'version': source['version'], 'files': files})
        os.rename(str(stage), str(target))
        return {'status': 'imported', 'tool': tool, 'path': str(target), 'files': len(files), 'bytes': total,
                'source_modified': False}
    finally:
        if stage.exists():
            shutil.rmtree(str(stage))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version='ocrun-workloads ' + suite.VERSION)
    commands = parser.add_subparsers(dest='action')
    for action in ('list', 'check'):
        commands.add_parser(action)
    for action in ('bind', 'unbind'):
        command = commands.add_parser(action)
        command.add_argument('--app', default='/root/ocrun')
        command.add_argument('--check', action='store_true')
    for action in ('import-mlc', 'import-spec'):
        command = commands.add_parser(action)
        command.add_argument('--archive', required=True)
        command.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Use root for package verification, binding and imports')
    if args.action in ('list', 'check'):
        data = suite.check()
        result = {'status': 'checked', 'version': data['version'], 'package_revision': data.get('package_revision', '1'),
                  'source_commit': data['source_commit'],
                  'tools': {k: {'version': v['version'], 'delivery': v.get('delivery', 'included')}
                            for k, v in data['sources'].items()}}
    elif args.action in ('bind', 'unbind'):
        result = suite.bind(Path(args.app), args.check, args.action == 'unbind')
    elif args.action in ('import-mlc', 'import-spec'):
        result = import_archive('mlc' if args.action == 'import-mlc' else 'cpu2017', args.archive, args.check)
    else:
        parser.error('Choose list, check, bind, unbind, import-mlc or import-spec')
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        print('ocrun-workloads: ' + str(error), file=sys.stderr)
        sys.exit(1)
