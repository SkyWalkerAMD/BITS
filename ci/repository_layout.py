"""Cloud-only repository move checks, independent of package construction."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
BASELINE = 'f56965cb22eb592a934b74e13fae7637fb5c2783'


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT)] + list(args))


def check():
    if sys.platform != 'linux' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Run repository validation only in authorized cloud Linux')
    files = git('ls-files', '-z').decode().rstrip('\0').split('\0')
    errors = []
    count = 0
    for name in files:
        if not name.endswith('.md'):
            continue
        path = ROOT / name
        fence = None
        for line, text in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            marker = re.match(r'^\s*(`{3,}|~{3,})', text)
            if marker:
                if fence is None:
                    fence = marker[1][0]
                elif marker[1][0] == fence:
                    fence = None
                continue
            if fence:
                continue
            for raw in re.findall(r'\[[^\]\n]*\]\(([^\s)]+)\)', text):
                url = urlsplit(raw.strip('<>'))
                if url.scheme or url.netloc or not url.path or url.path.startswith('/'):
                    continue
                target = (path.parent / unquote(url.path)).resolve()
                count += 1
                if ROOT not in target.parents and target != ROOT:
                    errors.append('{}:{}: link leaves repository: {}'.format(name, line, raw))
                elif not target.exists():
                    errors.append('{}:{}: missing link: {}'.format(name, line, raw))
    roots = {n for n in files if '/' not in n and n.endswith('.md')}
    if roots != {'README.md', 'CONTRIBUTING.md'}:
        errors.append('Put additional documentation under docs/: ' + repr(sorted(roots)))
    if any(n.startswith(('.cloud/', 'integrations/sckocp', 'drafts/')) for n in files):
        errors.append('Old/private working directories still tracked at retired locations')
    if any(n in files for n in ('.github/workflows/server-release.yml',
                                '.github/workflows/workloads-release.yml')):
        errors.append('Retired release workflows are still enabled')
    for name in files:
        if not name.startswith('.github/workflows/'):
            continue
        text = (ROOT / name).read_text()
        if '.cloud/' in text or 'integrations/sckocp' in text:
            errors.append('Workflow references retired path: ' + name)
        for relative in re.findall(r'(?<![\w./])((?:ci|research)/[\w./-]+\.(?:py|sh|Dockerfile))', text):
            if not (ROOT / relative).is_file():
                errors.append(name + ': missing fixture ' + relative)

    # Directory cleanup must not quietly change native implementation, fixed
    # tool archives or the scripts used to recognize original OCRUN installations.
    preserved = []
    originals = git('ls-tree', '-r', '--name-only', BASELINE).decode().splitlines()
    for old in originals:
        if old.startswith('integrations/sckocp') and not old.endswith('.md'):
            new = old.replace('integrations/', 'research/', 1)
        elif (old.startswith('integrations/mon-sensors/') and not old.endswith('.md') or
              old.startswith('workload_suite/vendor/') and old.endswith('.tar.gz')):
            new = old
        else:
            continue
        if not (ROOT / new).is_file() or git('show', BASELINE + ':' + old) != (ROOT / new).read_bytes():
            errors.append('Baseline content changed: ' + new)
        preserved.append(new)
    if errors:
        raise SystemExit('\n'.join(errors))
    output = ROOT / '.layout-results'
    output.mkdir(exist_ok=True)
    result = {'status': 'passed', 'source_commit': git('rev-parse', 'HEAD').decode().strip(),
              'baseline': BASELINE, 'relative_links': count, 'preserved_files': len(preserved),
              'scope': 'repository paths and byte preservation, not hardware acceptance'}
    (output / 'layout.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    check()
