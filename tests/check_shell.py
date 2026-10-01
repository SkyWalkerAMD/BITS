import ast
import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parents[1]
bash = sys.argv[1] if len(sys.argv) > 1 else '/bin/bash'
failed = []
for path in sorted(list(root.glob('*.sh')) + list((root / 'ci').glob('*.sh'))):
    result = subprocess.run([bash, '--noprofile', '--norc', '-n'], input=path.read_text(encoding='utf8'),
                            encoding='utf8', capture_output=True)
    print(str(path.relative_to(root)) + ': ' + ('PASS' if result.returncode == 0 else result.stderr))
    if result.returncode:
        failed.append(path.name)
for path in sorted(list((root / 'ocrun').glob('*.py')) + list((root / 'sckocp_api').glob('*.py')) +
                   list((root / 'bits_core/collector').glob('*.py'))):
    ast.parse(path.read_text(encoding='utf8'), filename=str(path), feature_version=(3, 6))
print('All runtime Python files parse with Python 3.6 grammar')
sys.exit(bool(failed))
