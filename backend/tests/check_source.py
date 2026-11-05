"""Check repository source syntax without imports, plus changed-file whitespace."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE_SUFFIXES = frozenset({'.py', '.js', '.cjs', '.mjs'})


def git_files(root, *options):
    result = subprocess.run(['git', 'ls-files', '-z', *options], cwd=root,
                            stdout=subprocess.PIPE, check=True, timeout=30)
    return sorted({os.fsdecode(name) for name in result.stdout.split(b'\0') if name})


def check_syntax(root, filenames):
    sources = [root / name for name in filenames
               if Path(name).suffix in SOURCE_SUFFIXES and (root / name).is_file()]
    if not any(path.suffix == '.py' for path in sources):
        raise RuntimeError('No Python sources found')
    if not any(path.suffix in {'.js', '.cjs', '.mjs'} for path in sources):
        raise RuntimeError('No JavaScript sources found')
    node = shutil.which('node')
    if node is None:
        raise RuntimeError('Node.js is required for JavaScript syntax checks')
    for path in sources:
        print(f'Syntax: {path.relative_to(root)}', flush=True)
        if path.suffix == '.py':
            compile(path.read_bytes(), str(path), 'exec', dont_inherit=True)
        else:
            subprocess.run([node, '--check', str(path)], cwd=root, check=True, timeout=30)


def check_whitespace(root, base):
    if base == '0' * 40:
        result = subprocess.run(['git', 'hash-object', '-t', 'tree', '--stdin'], cwd=root,
                                input=b'', stdout=subprocess.PIPE, check=True, timeout=30)
        base = result.stdout.decode('ascii').strip()
    subprocess.run(['git', '-c', 'core.whitespace=blank-at-eol,blank-at-eof,space-before-tab',
                    'diff', '--check', base, '--'], cwd=root, check=True, timeout=30)
    for name in git_files(root, '--others', '--exclude-standard'):
        result = subprocess.run(['git', '-c', 'core.whitespace=blank-at-eol,blank-at-eof,space-before-tab',
                                 'diff', '--no-index', '--check', '--', os.devnull, str(root / name)],
                                cwd=root, check=False, timeout=30)
        if result.returncode not in (0, 1):
            raise subprocess.CalledProcessError(result.returncode, result.args)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='HEAD', help='Git base for whitespace checks (default: HEAD)')
    options = parser.parse_args(argv)
    try:
        filenames = git_files(ROOT, '--cached', '--others', '--exclude-standard')
        check_syntax(ROOT, filenames)
        check_whitespace(ROOT, options.base)
    except (OSError, SyntaxError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Source checks failed: {error}', file=sys.stderr)
        return 1
    print('Source syntax and changed-file whitespace passed.', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
