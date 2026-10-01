"""Reuse cloud components only if their code is unchanged and all checks passed."""
import json
import os
import subprocess
import urllib.request


def api(path):
    request = urllib.request.Request('https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY'] + path,
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main():
    run = os.environ.get('COMPONENT_RUN') or os.environ['GITHUB_RUN_ID']
    commit = os.environ['GITHUB_SHA']
    if run != os.environ['GITHUB_RUN_ID']:
        if not run.isdigit():
            raise ValueError('Numeric cloud run required')
        previous = api('/actions/runs/' + run)
        if previous['path'] != '.github/workflows/distribution.yml':
            raise ValueError('Reuse only the integrated distribution workflow')
        commit = previous['head_sha']
        changes = subprocess.check_output(['git', 'diff', '--name-only', commit, os.environ['GITHUB_SHA']]).decode().splitlines()
        for name in changes:
            # Native wrapper code and its deployment/release documentation do
            # not alter the separately built server or workload components.
            if not (name.startswith(('distribution/', 'docs/releases/')) or name in (
                    'docs/deployment/DISTRIBUTION.md', 'docs/deployment/BITS.md', '.github/workflows/distribution.yml')):
                raise ValueError('Changed component code requires a full build: ' + name)
        jobs = api('/actions/runs/' + run + '/jobs?per_page=100')['jobs']
        selected = [j for j in jobs if j['name'].startswith(('tools /', 'center /'))]
        if len(selected) != 29 or any(j['conclusion'] != 'success' for j in selected):
            raise ValueError('Need all 16 tool/regression and 13 center jobs to have succeeded')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('run=' + run + '\ncommit=' + commit + '\n')
    print(json.dumps({'component_run': run, 'component_commit': commit,
                      'distribution_commit': os.environ['GITHUB_SHA']}))


if __name__ == '__main__':
    main()
