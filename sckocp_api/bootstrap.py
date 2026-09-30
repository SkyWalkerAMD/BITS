"""Managed CLI bootstrap, embedded in the trusted installed launcher.

This detects damaged/replaced modules and permission drift; it is not a
signature, code encryption, or a defence against replacement of the launcher
or the system interpreter by root. SDK hosts own their import trust boundary.
"""
import datetime
import hashlib
import importlib.abc
import importlib.util
import json
import os
import stat
import sys


LIMIT = 2 * 1024 * 1024


def _trusted(info, directory=False):
    if info.st_uid not in (0, os.geteuid()):
        raise ValueError('Untrusted owner')
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('Not a directory')
        if info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
            raise ValueError('Writable parent')
    elif (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
          info.st_mode & 0o7022 or info.st_size > LIMIT):
        raise ValueError('Untrusted module')


def _sources(prefix, expected):
    """Validate all modules before any package code runs, retaining their bytes.

    openat pins each directory; O_NOFOLLOW rejects substituted links, including
    FIFOs without blocking. No bytecode cache or import from the package's disk
    path is used after this check, so a path swap cannot redirect an import.
    """
    if not os.path.isabs(prefix) or os.path.normpath(prefix) != prefix:
        raise ValueError('Invalid installation prefix')
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _trusted(os.fstat(descriptor), directory=True)
        for part in (prefix + '/sckocp_api').split('/'):
            if not part:
                continue
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=descriptor)
            try:
                _trusted(os.fstat(child), directory=True)
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        sources = {}
        for name, digest in expected.items():
            if not name.endswith('.py') or '/' in name or '\\' in name or name.startswith('.'):
                raise ValueError('Invalid module inventory')
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                         dir_fd=descriptor)
            try:
                _trusted(os.fstat(fd))
                with os.fdopen(fd, 'rb') as stream:
                    fd = None
                    data = stream.read(LIMIT + 1)
                if len(data) > LIMIT or hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError('Module differs from installed inventory')
                module = 'sckocp_api' if name == '__init__.py' else 'sckocp_api.' + name[:-3]
                sources[module] = (data, prefix + '/sckocp_api/' + name)
            finally:
                if fd is not None:
                    os.close(fd)
        return sources
    finally:
        os.close(descriptor)


class _VerifiedModules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, sources):
        self.sources = sources

    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'sckocp_api' or fullname.startswith('sckocp_api.'):
            if fullname not in self.sources:
                raise ImportError('Module is not in the verified API inventory')
            return importlib.util.spec_from_loader(fullname, self, is_package=fullname == 'sckocp_api')
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        source, filename = self.sources[module.__name__]
        module.__file__ = filename
        exec(compile(source, filename, 'exec', dont_inherit=True), module.__dict__)


def launch(prefix, expected):
    try:
        if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
            raise ValueError('Isolated interpreter required')
        if any(name == 'sckocp_api' or name.startswith('sckocp_api.') for name in sys.modules):
            raise ValueError('API code loaded before verification')
        sources = _sources(prefix, expected)
    except (OSError, ValueError):
        print(json.dumps({'schema': 'sckocp-api-v1', 'status': 'integrity_error',
            'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(
                timespec='milliseconds').replace('+00:00', 'Z'),
            'data': None, 'error': 'The installed API failed its runtime integrity check.'},
            separators=(',', ':')))
        return 1
    loader = _VerifiedModules(sources)
    sys.meta_path.insert(0, loader)
    from sckocp_api.cli import main
    return main()
