"""Run Flask integration tests against a freshly provisioned, disposable MySQL."""

import argparse
import builtins
import io
import subprocess
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from run_tests import RejectModelImports, guarded_open


MYSQL_DIR = Path(__file__).resolve().parent / 'mysql'


class RejectModelImportsExceptPDF(RejectModelImports):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] == 'fitz':
            return None
        return super().find_spec(fullname, path, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeat', type=int, default=1)
    options = parser.parse_args()
    if options.repeat < 1:
        parser.error('--repeat must be positive')
    with patch.object(sys, 'path', [str(MYSQL_DIR), *sys.path]):
        from mysql_support import DisposableMySQL, database_sandbox

        try:
            with DisposableMySQL().provision() as target, database_sandbox(target) as violations:
                with ExitStack() as resources:
                    model_guard = RejectModelImportsExceptPDF()
                    resources.enter_context(patch.object(sys, 'meta_path', [model_guard, *sys.meta_path]))
                    resources.enter_context(patch.object(builtins, 'open', guarded_open(builtins.open)))
                    resources.enter_context(patch.object(io, 'open', guarded_open(io.open)))
                    for iteration in range(options.repeat):
                        print(f'MySQL integration suite {iteration + 1}/{options.repeat}', flush=True)
                        suite = unittest.defaultTestLoader.discover(str(MYSQL_DIR), pattern='test_api.py')
                        if not suite.countTestCases():
                            raise RuntimeError('No MySQL integration tests discovered')
                        result = unittest.TextTestRunner(verbosity=2).run(suite)
                        if violations:
                            raise RuntimeError('Integration isolation boundary was violated')
                        if not result.wasSuccessful():
                            return 1
            return 0
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            print(f'MySQL integration run failed ({type(error).__name__}). '
                  'Ensure Docker with Linux containers is running and the test dependencies are installed.',
                  file=sys.stderr)
            if isinstance(error, subprocess.CalledProcessError):
                print(error.stderr, file=sys.stderr)
            return 2


if __name__ == '__main__':
    sys.exit(main())
