"""Render the synthetic acceptance fixture only on the authorized cloud runner."""
import json
import os
from pathlib import Path
import shutil
import sys
# This is a browser-test helper, not an installed finalizer entry. Keep the
# finalizer's queue.py out of the stdlib queue import used by asyncio threads.
sys.path = [p for p in sys.path if Path(p).resolve() != Path(__file__).resolve().parent]
from playwright.sync_api import sync_playwright

if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
    raise SystemExit('Cloud Linux only')
root = Path(sys.argv[1]).resolve()
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=shutil.which('google-chrome'), args=['--no-sandbox'])
    context = browser.new_context(viewport={'width': 1440, 'height': 1100}, offline=True)
    page = context.new_page()
    page.goto((root / 'report-preview.html').as_uri())
    page.evaluate('document.fonts.ready')
    checks = page.evaluate('''() => ({scripts:document.scripts.length,
        width:document.documentElement.scrollWidth, viewport:innerWidth,
        external: [...document.querySelectorAll('[src],[href]')].length,
        headings:[...document.querySelectorAll('h2')].map(x=>x.textContent)})''')
    assert checks['scripts'] == 0 and checks['external'] == 0
    assert checks['width'] <= checks['viewport'] + 1 and len(checks['headings']) == 7
    page.screenshot(path=str(root / 'report-preview.png'), full_page=True)
    page.pdf(path=str(root / 'report-print-preview.pdf'), prefer_css_page_size=True, print_background=True)
    checks['status'] = 'passed'
    checks['browser'] = browser.version
    (root / 'report-visual.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2))
    browser.close()
