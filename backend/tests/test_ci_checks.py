"""Fail-closed source checks and regression-runner failure propagation."""

import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import check_source
import run_tests


class SourceCheckTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.python = self.root / 'example.py'
        self.javascript = self.root / 'example.js'
        self.python.write_text("raise AssertionError('Must not import source')\n", encoding='utf-8')
        self.javascript.write_text('const example = 1;\n', encoding='utf-8')
        self.filenames = ['example.py', 'example.js', 'deleted.py', 'ignored.txt']

    def test_compiles_without_executing_or_creating_bytecode_and_checks_javascript(self):
        with patch.object(check_source.shutil, 'which', return_value='node'), \
                patch.object(check_source.subprocess, 'run') as command:
            check_source.check_syntax(self.root, self.filenames)
        command.assert_called_once_with(['node', '--check', str(self.javascript)],
                                        cwd=self.root, check=True, timeout=30)
        self.assertFalse((self.root / '__pycache__').exists())

    def test_broken_python_fails_without_importing_application_dependencies(self):
        self.python.write_text('def broken(:\n', encoding='utf-8')
        with patch.object(check_source.shutil, 'which', return_value='node'), \
                self.assertRaises(SyntaxError):
            check_source.check_syntax(self.root, self.filenames)

    def test_repeated_imports_in_the_same_block_fail_with_location(self):
        for source, name, line in (
            ('import os\nimport os\n', 'os', 2),
            ('import os, os\n', 'os', 1),
            ('from pathlib import Path\nfrom pathlib import Path\n', 'Path', 2),
            ('def helper():\n    import os\n    import os\n', 'os', 3),
        ):
            with self.subTest(source=source), self.assertRaisesRegex(
                    ValueError, f'example.py:{line}: duplicate import {name!r}'):
                check_source.check_python(source, 'example.py')

    def test_import_checks_preserve_branches_aliases_scopes_and_rebinding(self):
        source = '''import os
import os as operating_system
from .paths import Path
from paths import Path
if condition:
    import json
else:
    import json
try:
    import package
except ImportError:
    import package
def helper():
    import os
del os
import os
'''
        check_source.check_python(source, 'example.py')
        self.assertFalse((self.root / '__pycache__').exists())

    def test_python_semantic_syntax_errors_are_rejected(self):
        with self.assertRaises(SyntaxError):
            check_source.check_python('return 1\n', 'example.py')

    def test_tracked_generated_files_are_rejected_even_when_missing_on_disk(self):
        for name in ('nested/__pycache__/module.pyc', 'module.pyc', 'module.pyo',
                     'nested/__pycache__/unexpected.txt'):
            with self.subTest(name=name), \
                    patch.object(check_source, 'git_files', return_value=[name]) as inventory, \
                    self.assertRaisesRegex(RuntimeError, 'removed from the Git index'):
                check_source.check_generated_files(self.root)
            inventory.assert_called_once_with(self.root, '--cached')
        with patch.object(check_source, 'git_files', return_value=['module.py', 'frontend/script.js']):
            check_source.check_generated_files(self.root)

    def test_missing_node_and_empty_source_inventory_are_errors(self):
        for filenames, node in (([], 'node'), (['example.py'], 'node'), (self.filenames, None)):
            with self.subTest(filenames=filenames, node=node), \
                    patch.object(check_source.shutil, 'which', return_value=node), \
                    self.assertRaises(RuntimeError):
                check_source.check_syntax(self.root, filenames)

    def test_inventory_includes_nonignored_new_files_and_deduplicates(self):
        result = Mock(stdout=b'example.py\0new file.js\0example.py\0')
        with patch.object(check_source.subprocess, 'run', return_value=result) as command:
            self.assertEqual(check_source.git_files(self.root, '--cached', '--others', '--exclude-standard'),
                             ['example.py', 'new file.js'])
        self.assertEqual(command.call_args.args[0],
                         ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'])

    def test_whitespace_checks_base_and_untracked_files_with_checked_exit_codes(self):
        with patch.object(check_source, 'git_files', return_value=['new file.py']), \
                patch.object(check_source.subprocess, 'run', return_value=Mock(returncode=1)) as command:
            check_source.check_whitespace(self.root, 'base-commit')
        self.assertIn('base-commit', command.call_args_list[0].args[0])
        self.assertIn('--no-index', command.call_args_list[1].args[0])
        self.assertEqual(command.call_args_list[1].args[0][-1], str(self.root / 'new file.py'))
        self.assertTrue(command.call_args_list[0].kwargs['check'])
        self.assertFalse(command.call_args_list[1].kwargs['check'])

    def test_new_file_whitespace_errors_and_git_errors_are_not_ignored(self):
        for status in (2, 3, 128):
            with self.subTest(status=status), \
                    patch.object(check_source, 'git_files', return_value=['new file.py']), \
                    patch.object(check_source.subprocess, 'run', return_value=Mock(returncode=status)), \
                    self.assertRaises(subprocess.CalledProcessError):
                check_source.check_whitespace(self.root, 'HEAD')

    def test_initial_push_checks_against_empty_tree(self):
        with patch.object(check_source, 'git_files', return_value=[]), \
                patch.object(check_source.subprocess, 'run', return_value=Mock(stdout=b'empty-tree\n')) as command:
            check_source.check_whitespace(self.root, '0' * 40)
        self.assertEqual(command.call_args_list[0].kwargs['input'], b'')
        self.assertIn('empty-tree', command.call_args_list[1].args[0])

    def test_syntax_whitespace_and_missing_prerequisites_return_nonzero(self):
        for failure in (SyntaxError('broken Python'), FileNotFoundError('missing git'),
                        subprocess.CalledProcessError(1, ['node', '--check']),
                        subprocess.CalledProcessError(2, ['git', 'diff', '--check']),
                        subprocess.TimeoutExpired(['node'], 30)):
            for stage in ('check_generated_files', 'check_syntax', 'check_whitespace'):
                with self.subTest(failure=type(failure).__name__, stage=stage), \
                        patch.object(check_source, 'git_files', return_value=self.filenames), \
                        patch.object(check_source, 'check_generated_files'), \
                        patch.object(check_source, 'check_syntax'), \
                        patch.object(check_source, stage, side_effect=failure), \
                        patch('sys.stderr', new_callable=io.StringIO):
                    self.assertEqual(check_source.main([]), 1)


class RunnerExitTests(unittest.TestCase):
    def test_failed_assertions_and_errors_fail_the_ci_runner(self):
        for failure in (AssertionError('Broken API contract'), RuntimeError('Broken permission fixture')):
            with self.subTest(failure=type(failure).__name__):
                def broken_test():
                    raise failure
                failed_test = unittest.FunctionTestCase(broken_test)
                suite = unittest.TestSuite([failed_test])
                with patch.object(run_tests.unittest.defaultTestLoader, 'discover', return_value=suite), \
                        patch('sys.argv', ['run_tests.py', '--repeat', '2']), \
                        patch('sys.stderr', new_callable=io.StringIO):
                    self.assertEqual(run_tests.main(), 1)

    def test_empty_test_discovery_is_not_success(self):
        with patch.object(run_tests.unittest.defaultTestLoader, 'discover', return_value=unittest.TestSuite()), \
                patch('sys.argv', ['run_tests.py']), patch('sys.stderr', new_callable=io.StringIO), \
                self.assertRaises(SystemExit) as failure:
            run_tests.main()
        self.assertEqual(failure.exception.code, 2)


class RepositoryHygieneTests(unittest.TestCase):
    def test_ignore_rules_and_index_guard_prevent_bytecode_from_returning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '--quiet'], cwd=root, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            (root / '.gitignore').write_bytes((check_source.ROOT / '.gitignore').read_bytes())
            ignored = ['module.pyc', 'module.pyo', 'nested/deeper/module.pyc',
                       'nested/deeper/module.pyo', 'nested/__pycache__/unexpected.txt']
            result = subprocess.run(['git', 'check-ignore', '-z', '--stdin'], cwd=root,
                                    input=b'\0'.join(name.encode() for name in
                                                     [*ignored, 'nested/module.py']) + b'\0',
                                    stdout=subprocess.PIPE, check=True, timeout=30)
            self.assertEqual(result.stdout.decode().split('\0')[:-1], ignored)

            bytecode = root / 'module.pyc'
            bytecode.write_bytes(b'generated bytecode fixture')
            subprocess.run(['git', 'add', '--force', '--', bytecode.name], cwd=root,
                           check=True, timeout=30)
            with self.assertRaisesRegex(RuntimeError, 'module.pyc'):
                check_source.check_generated_files(root)
            subprocess.run(['git', 'rm', '--cached', '--quiet', '--', bytecode.name],
                           cwd=root, check=True, timeout=30)
            check_source.check_generated_files(root)
            self.assertTrue(bytecode.exists())
            self.assertEqual(check_source.git_files(root, '--others', '--exclude-standard'),
                             ['.gitignore'])


if __name__ == '__main__':
    unittest.main()
