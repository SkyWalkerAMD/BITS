"""Isolated report launcher. Consumes existing .mon files, never hardware."""
import argparse
import contextlib
import csv
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import resource
import shutil
import sys
import tempfile

ROOT = Path(__file__).absolute().parent
sys.path.insert(0, str(ROOT))
from common import VERSION, atomic, digest, directory, read, regular


def hash_file(path):
    import hashlib
    checksum = hashlib.sha256()
    with open(str(path), 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def dependencies():
    directory(ROOT)
    directory(ROOT / "vendor")
    sys.path.insert(0, str(ROOT / "vendor"))
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"
    import numpy
    import pandas
    import openpyxl
    import xlsxwriter
    return {"numpy": numpy.__version__, "pandas": pandas.__version__,
            "openpyxl": openpyxl.__version__, "xlsxwriter": xlsxwriter.__version__}


def snapshot(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    regular(info, path=path)
    if info.st_uid != os.geteuid():
        raise ValueError("Output workbook must belong to the current user")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def validate(data):
    text = data.decode("utf-8")
    lines = text.splitlines()
    if len(lines) < 3 or not lines[0].startswith("#-") or not lines[1].startswith("#="):
        raise ValueError("Expected two OCRUN .mon header lines and data")
    rows = list(csv.reader(lines[2:], strict=True))
    if not 1 <= len(rows) <= 250000:
        raise ValueError("Report requires 1 to 250000 monitoring rows")
    for row in rows:
        if len(row) != 11 or any(
                len(cell) > 1024 or any(ord(ch) < 32 or ch == ',' for ch in cell)
                for cell in row):
            raise ValueError("Invalid monitoring row; expected 11 plain fields")
    return len(rows)


def generate(source, output, sheet):
    source = Path(os.path.abspath(source))
    output = Path(os.path.abspath(output))
    directory(output.parent)
    if output.suffix.lower() != ".xlsx" or source == output:
        raise ValueError("Output must be a separate .xlsx file")
    if (not sheet or len(sheet) > 31 or re.search(r"[\[\]:*?/\\\x00-\x1f]", sheet)
            or sheet.startswith("'") or sheet.endswith("'")):
        raise ValueError("Invalid worksheet name")
    lockpath = output.parent / ("." + output.name + ".report.lock")
    fd = os.open(str(lockpath), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    work = None
    try:
        regular(os.fstat(fd), private=True, path=lockpath)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = snapshot(output)
        # Keep the established small-report layout. Large reports stream into
        # bounded sheets instead of constructing a full pandas frame in memory.
        source_info = source.lstat()
        regular(source_info, path=source)
        data = read(source, 2 * 1024 * 1024) if source_info.st_size <= 2 * 1024 * 1024 else None
        count = validate(data) if data is not None else None
        dependencies()
        # Import only our bundled, reviewed renderer and its private dependencies.
        import analyser
        work = Path(tempfile.mkdtemp(prefix=".report-work-", dir=str(output.parent)))
        sample = work / "input.mon"
        workbook = work / "output.xlsx"
        if data is not None:
            atomic(sample, data, replace=False)
            with contextlib.redirect_stdout(io.StringIO()):
                analyser.main(str(sample), str(workbook), sheet)
            source_digest = digest(data)
        else:
            import streaming
            count, source_digest = streaming.render(source, workbook, sheet)
        os.chmod(str(workbook), 0o600)
        with open(str(workbook), "rb") as stream:
            os.fsync(stream.fileno())
        if snapshot(output) != before:
            raise ValueError("Output workbook changed during generation")
        backup = None
        if before is not None:
            backup_root = output.parent / ".mon-sensors-report-backups"
            try:
                backup_root.mkdir(mode=0o700)
            except FileExistsError:
                pass
            directory(backup_root)
            if snapshot(output) != before:
                raise ValueError("Output workbook changed during backup")
            backup_fd, backup_name = tempfile.mkstemp(prefix=output.stem + "-", suffix=".xlsx",
                                                      dir=str(backup_root))
            with os.fdopen(backup_fd, "wb") as stream:
                with open(str(output), 'rb') as old:
                    shutil.copyfileobj(old, stream, 1024 * 1024)
                stream.flush()
                os.fsync(stream.fileno())
            backup = backup_name
            if snapshot(output) != before:
                raise ValueError("Output workbook changed before publication")
            os.replace(str(workbook), str(output))
        else:
            # Do not overwrite a file that appeared while the report was running.
            os.link(str(workbook), str(output))
            workbook.unlink()
        return {"status": "ok", "rows": count, "output": str(output),
                "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "source_sha256": source_digest, "sha256": hash_file(output), "backup": backup}
    finally:
        if work is not None:
            shutil.rmtree(str(work))  # Unique directory created by this invocation only.
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?")
    parser.add_argument("output", nargs="?")
    parser.add_argument("sheet", nargs="?", default="Monitoring")
    parser.add_argument("--check", action="store_true", help="Check the private report dependencies")
    parser.add_argument("--version", action="version", version="mon-sensors-report " + VERSION)
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 6) or sys.platform != "linux":
        parser.error("This offline build requires Linux CPython 3.6")
    if args.check:
        result = {"status": "ok", "version": VERSION, "python": sys.version.split()[0],
                  "dependencies": dependencies()}
    else:
        if not args.input or not args.output:
            parser.error("input.mon and output.xlsx are required")
        result = generate(args.input, args.output, args.sheet)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, ImportError) as error:
        print("mon-sensors-report: " + str(error), file=sys.stderr)
        sys.exit(1)
