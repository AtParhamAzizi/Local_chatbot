"""Run the default suite with external services and real model imports forbidden."""

import argparse
import builtins
import importlib.abc
import io
import os
import socket
import sys
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

import pymysql


TEST_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TEST_DIR.parent
MODEL_DIRS = (BACKEND_DIR / 'model', BACKEND_DIR / 'modelgen', BACKEND_DIR / 'STT' / 'model')
MODEL_PACKAGES = frozenset({'torch', 'transformers', 'faster_whisper', 'ctranslate2',
                            'huggingface_hub', 'av', 'fitz'})


class RejectModelImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in MODEL_PACKAGES:
            raise AssertionError(f'Real model/PDF dependency import is forbidden: {fullname}')
        return None


def reject_external_work(*args, **kwargs):
    raise AssertionError('Network and real database access are forbidden in the default suite')


def guarded_open(original):
    def open_without_models(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(file)).resolve()
            if any(path.is_relative_to(directory) for directory in MODEL_DIRS):
                raise AssertionError(f'Model file access is forbidden: {path}')
        return original(file, *args, **kwargs)
    return open_without_models


@contextmanager
def offline_guards():
    with ExitStack() as resources:
        resources.enter_context(patch.object(sys, 'path', [str(TEST_DIR), *sys.path]))
        resources.enter_context(patch.object(sys, 'meta_path', [RejectModelImports(), *sys.meta_path]))
        resources.enter_context(patch.object(builtins, 'open', guarded_open(builtins.open)))
        resources.enter_context(patch.object(io, 'open', guarded_open(io.open)))
        resources.enter_context(patch.object(pymysql, 'connect', reject_external_work))
        for name in ('create_connection', 'getaddrinfo'):
            resources.enter_context(patch.object(socket, name, reject_external_work))
        for name in ('connect', 'connect_ex', 'sendto'):
            resources.enter_context(patch.object(socket.socket, name, reject_external_work))
        yield


def test_identifiers(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from test_identifiers(item)
        else:
            yield item.id()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeat', type=int, default=1, help='Repeat discovery in the same process')
    parser.add_argument('--pattern', default='test_*.py', help='unittest discovery filename pattern')
    parser.add_argument('-v', '--verbose', action='store_true')
    options = parser.parse_args()
    if options.repeat < 1:
        parser.error('--repeat must be positive')

    expected_identifiers = None
    with offline_guards():
        for iteration in range(options.repeat):
            print(f'Offline suite {iteration + 1}/{options.repeat}', flush=True)
            suite = unittest.defaultTestLoader.discover(str(TEST_DIR), pattern=options.pattern)
            if not suite.countTestCases():
                parser.error('No tests matched the discovery pattern')
            identifiers = list(test_identifiers(suite))
            if expected_identifiers is not None and identifiers != expected_identifiers:
                print('Test inventory changed between runs; check leaked test fixtures.', file=sys.stderr)
                return 1
            expected_identifiers = identifiers
            result = unittest.TextTestRunner(verbosity=2 if options.verbose else 1).run(suite)
            if not result.wasSuccessful():
                return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
