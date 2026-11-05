"""Offline checks for locked dependency profiles; no wheel downloads or model imports."""

from copy import deepcopy
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from packaging.requirements import Requirement


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('dependency_profiles', ROOT / 'scripts' / 'dependencies.py')
profiles = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profiles)


def distribution(name, version):
    return SimpleNamespace(metadata={'Name': name}, version=version)


def input_requirements(path):
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.split('#', 1)[0].strip()
        if line.startswith('-r '):
            yield from input_requirements((path.parent / line[3:]).resolve())
        elif line and not line.startswith('-'):
            yield Requirement(line)


def report_for(profile='cpu'):
    versions = {name: '1.0' for name in profiles.RUNTIME}
    versions['torch'] = '2.7.1+cpu' if profile == 'cpu' else '2.7.1+cu118'
    if profile == 'cu118':
        versions.update(torchaudio='2.7.1+cu118', bitsandbytes='0.47.0', accelerate='1.15.0')
    return {
        'environment': {'sys_platform': 'win32', 'python_version': '3.13',
                        'implementation_name': 'cpython', 'platform_machine': 'AMD64'},
        'install': [
            {'metadata': {'name': name, 'version': version},
             'download_info': {'url': f'https://example.invalid/{name}.whl',
                               'archive_info': {'hashes': {'sha256': 'a' * 64}}}}
            for name, version in versions.items()
        ],
    }


class LockTests(unittest.TestCase):
    def test_locks_satisfy_the_current_application_inputs_on_each_target(self):
        for profile in ('cpu', 'cu118'):
            paths = [ROOT / 'requirements-cpu.txt'] if profile == 'cpu' else [
                ROOT / 'requirements-cuda.txt', ROOT / 'requirements-docker.txt']
            for target, (system, version) in profiles.TARGETS.items():
                locked = profiles.read_lock(profiles.LOCKS / f'{profile}-{target}.txt')
                environment = {
                    'sys_platform': system, 'python_version': version, 'extra': '',
                    'python_full_version': '3.13.14' if system == 'win32' else '3.12.16',
                    'platform_system': 'Windows' if system == 'win32' else 'Linux',
                    'platform_machine': 'AMD64' if system == 'win32' else 'x86_64',
                    'os_name': 'nt' if system == 'win32' else 'posix',
                    'implementation_name': 'cpython', 'platform_python_implementation': 'CPython',
                }
                for path in paths:
                    for requirement in input_requirements(path):
                        if requirement.marker and not requirement.marker.evaluate(environment):
                            continue
                        with self.subTest(profile=profile, target=target, requirement=str(requirement)):
                            name = profiles.canonical(requirement.name)
                            self.assertIn(name, locked)
                            self.assertIn(locked[name], requirement.specifier)

    def test_all_four_locks_are_complete_exact_and_consistent_with_constraints(self):
        constraints = dict(line.split('==') for line in
                           (ROOT / 'requirements' / 'constraints.txt').read_text(encoding='utf-8').splitlines())
        for profile in ('cpu', 'cu118'):
            for target in profiles.TARGETS:
                with self.subTest(profile=profile, target=target):
                    locked = profiles.read_lock(profiles.LOCKS / f'{profile}-{target}.txt')
                    self.assertFalse(profiles.RUNTIME - locked.keys())
                    self.assertEqual(locked['torch'], '2.7.1+cpu' if profile == 'cpu' else '2.7.1+cu118')
                    for name, version in locked.items():
                        if name not in ('torch', 'torchaudio'):
                            self.assertEqual(constraints[name], version)
                    if profile == 'cpu':
                        self.assertFalse({'bitsandbytes', 'accelerate', 'torchaudio', 'triton'} & locked.keys())
                        self.assertFalse(any(name.startswith('nvidia-') for name in locked))
                    else:
                        self.assertEqual(locked['torchaudio'], locked['torch'])
                        self.assertEqual(locked['bitsandbytes'], '0.47.0')
                        if target.startswith('linux'):
                            self.assertEqual(locked['nvidia-cuda-runtime-cu11'], '11.8.89')
                            self.assertEqual(locked['triton'], '3.3.1')

    def test_native_reports_record_sorted_hash_pins_and_rsa_extra(self):
        for profile in ('cpu', 'cu118'):
            with self.subTest(profile=profile):
                text = profiles.record_lock(report_for(profile), profile, 'windows-py313')
                names = [line.split('==')[0].split('[')[0] for line in text.splitlines()]
                self.assertEqual(names, sorted(names))
                self.assertIn('pymysql[rsa]==1.0 --hash=sha256:', text)
                self.assertNotIn('example.invalid', text)

    def test_wrong_native_report_environment_is_rejected(self):
        for key, value in (('sys_platform', 'linux'), ('python_version', '3.14'),
                           ('platform_machine', 'aarch64'), ('implementation_name', 'pypy')):
            report = report_for()
            report['environment'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                profiles.record_lock(report, 'cpu', 'windows-py313')

    def test_incomplete_unhashed_yanked_editable_or_source_reports_fail(self):
        original = report_for()
        cases = []
        for field in ('is_yanked', 'is_editable'):
            report = deepcopy(original)
            report['install'][0][field] = True
            cases.append(report)
        report = deepcopy(original)
        report['install'][0]['download_info']['archive_info']['hashes']['sha256'] = 'not-a-hash'
        cases.append(report)
        report = deepcopy(original)
        report['install'][0]['download_info']['url'] = 'https://example.invalid/package.tar.gz'
        cases.append(report)
        report = deepcopy(original)
        report['install'].pop()
        cases.append(report)
        report = deepcopy(original)
        report['install'].append(report['install'][0])
        cases.append(report)
        for report in cases:
            with self.subTest(report=report['install'][0]), self.assertRaises(ValueError):
                profiles.record_lock(report, 'cpu', 'windows-py313')

    def test_profile_mixups_and_unmatched_audio_are_rejected(self):
        for report, profile in ((report_for('cpu'), 'cu118'), (report_for('cu118'), 'cpu')):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                profiles.record_lock(report, profile, 'windows-py313')
        report = report_for('cu118')
        next(item for item in report['install'] if item['metadata']['name'] == 'torchaudio')['metadata']['version'] = '2.8.0'
        with self.assertRaises(ValueError):
            profiles.record_lock(report, 'cu118', 'windows-py313')

    def test_malformed_empty_and_duplicate_locks_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lock.txt'
            for contents in ('', 'torch>=2.4\n', '--extra-index-url https://example.invalid\n',
                             'torch==2.7.1 --hash=sha256:missing\n',
                             ('torch==2.7.1 --hash=sha256:' + 'a' * 64 + '\n') * 2):
                path.write_text(contents, encoding='utf-8')
                with self.subTest(contents=contents), self.assertRaises(ValueError):
                    profiles.read_lock(path)


class ProfileCommandTests(unittest.TestCase):
    def test_unsupported_platforms_reject_without_installing(self):
        with patch.object(profiles.platform, 'machine', return_value='aarch64'), self.assertRaises(RuntimeError):
            profiles.native_target()

    def test_existing_developer_environment_and_system_python_are_rejected(self):
        for installed, prefix in (([distribution('torch', '2.7.1')], 'fresh'), ([], sys.base_prefix)):
            with self.subTest(prefix=prefix), \
                    patch.object(profiles, 'native_target', return_value='windows-py313'), \
                    patch.object(profiles.importlib.metadata, 'distributions', return_value=installed), \
                    patch.object(sys, 'prefix', prefix), \
                    patch.object(profiles.subprocess, 'run') as command, self.assertRaises(RuntimeError):
                profiles.install('cpu', [])
            command.assert_not_called()

    def test_offline_install_uses_hashes_wheels_and_no_indexes_then_checks_dependencies(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(profiles, 'native_target', return_value='windows-py313'), \
                patch.object(profiles.importlib.metadata, 'distributions', return_value=[distribution('pip', '25.1')]), \
                patch.object(sys, 'prefix', 'fresh'), patch.object(profiles.subprocess, 'run') as command:
            profiles.install('cpu', [Path(directory)])
        arguments = command.call_args_list[0].args[0]
        for flag in ('--require-hashes', '--only-binary=:all:', '--no-index', '--isolated'):
            self.assertIn(flag, arguments)
        self.assertNotIn('--extra-index-url', arguments)
        self.assertEqual(command.call_args_list[1].args[0][-2:], ['pip', 'check'])
        self.assertTrue(all(invocation.kwargs['check'] for invocation in command.call_args_list))

    def test_failed_install_is_not_replaced_with_a_successful_check(self):
        with patch.object(profiles, 'native_target', return_value='windows-py313'), \
                patch.object(profiles.importlib.metadata, 'distributions', return_value=[]), \
                patch.object(sys, 'prefix', 'fresh'), \
                patch.object(profiles.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['pip'])) as command, \
                self.assertRaises(subprocess.CalledProcessError):
            profiles.install('cpu', [])
        command.assert_called_once()

    def test_verify_rejects_missing_wrong_and_extra_packages_before_imports(self):
        locked = profiles.read_lock(profiles.LOCKS / 'cpu-windows-py313.txt')
        exact = [distribution(name, version) for name, version in locked.items()]
        for installed in (exact[:-1], exact + [distribution('qwen-tts', '0.1.1')],
                          [distribution(name, 'wrong') for name in locked]):
            with self.subTest(size=len(installed)), \
                    patch.object(profiles, 'native_target', return_value='windows-py313'), \
                    patch.object(profiles.importlib.metadata, 'distributions', return_value=installed), \
                    patch.object(profiles, 'smoke') as smoke, self.assertRaises(RuntimeError):
                profiles.verify('cpu', False)
            smoke.assert_not_called()

    def test_exact_environment_runs_check_and_preserves_explicit_hardware_mode(self):
        locked = profiles.read_lock(profiles.LOCKS / 'cu118-windows-py313.txt')
        installed = [distribution(name, version) for name, version in locked.items()] + [distribution('pip', '25.1')]
        with patch.object(profiles, 'native_target', return_value='windows-py313'), \
                patch.object(profiles.importlib.metadata, 'distributions', return_value=installed), \
                patch.object(profiles.subprocess, 'run') as command, patch.object(profiles, 'smoke') as smoke:
            profiles.verify('cu118', False)
        smoke.assert_called_once_with('cu118', False)
        self.assertTrue(command.call_args.kwargs['check'])

    def test_command_failures_are_nonzero_and_network_guard_fails_closed(self):
        with patch.object(profiles, 'verify', side_effect=RuntimeError('bad environment')), \
                patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(profiles.main(['verify', '--profile', 'cpu']), 1)
        with self.assertRaises(RuntimeError):
            profiles.reject_network()


if __name__ == '__main__':
    unittest.main()
