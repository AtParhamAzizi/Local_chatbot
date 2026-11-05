"""Install, verify, or record native, hash-pinned application dependency profiles."""

import argparse
from contextlib import ExitStack
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import socket
import struct
import subprocess
import sys
import sysconfig
from unittest.mock import patch
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
LOCKS = ROOT / 'requirements' / 'locks'
TARGETS = {'windows-py313': ('win32', '3.13'), 'linux-py312': ('linux', '3.12')}
BOOTSTRAP = frozenset({'pip', 'wheel'})
RUNTIME = frozenset({'flask', 'flask-cors', 'flask-sock', 'simple-websocket', 'pymysql', 'cryptography',
                     'numpy', 'torch', 'transformers', 'pymupdf', 'faster-whisper', 'av', 'gunicorn'})


def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def native_target():
    python_version = f'{sys.version_info.major}.{sys.version_info.minor}'
    target = next((name for name, (system, version) in TARGETS.items()
                   if system == sys.platform and version == python_version), None)
    if (target is None or sys.implementation.name != 'cpython'
            or platform.machine().lower() not in ('amd64', 'x86_64') or struct.calcsize('P') != 8
            or sysconfig.get_config_var('Py_GIL_DISABLED')):
        raise RuntimeError('Recorded locks require CPython 3.13 Windows x64 or CPython 3.12 Linux x86-64')
    return target


def read_lock(path):
    result = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([A-Za-z0-9_.-]+(?:\[rsa\])?)==([^ ]+) --hash=sha256:([0-9a-f]{64})', line)
        if match is None:
            raise ValueError(f'Invalid pinned requirement in {path.name}')
        name = canonical(match[1].split('[')[0])
        if name in result:
            raise ValueError(f'Duplicate requirement: {name}')
        result[name] = match[2]
    if not result:
        raise ValueError('Empty dependency lock')
    return result


def record_lock(report, profile, target):
    environment = report['environment']
    system, version = TARGETS[target]
    if (environment['sys_platform'] != system or environment['python_version'] != version
            or environment['implementation_name'] != 'cpython'
            or environment['platform_machine'].lower() not in ('amd64', 'x86_64')):
        raise ValueError('Resolve natively on the recorded target; cross-platform reports are not sufficient')
    rows = {}
    for item in report['install']:
        if item.get('is_yanked') or item.get('is_editable'):
            raise ValueError('Yanked and editable packages are not reproducible release inputs')
        download = item['download_info']
        if not unquote(urlsplit(download['url']).path).endswith('.whl'):
            raise ValueError('Resolve wheels only, not source builds')
        digest = download['archive_info']['hashes']['sha256']
        if not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('Every resolved wheel needs a SHA-256 digest')
        metadata = item['metadata']
        name = canonical(metadata['name'])
        if (name in rows or not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', name)
                or not re.fullmatch('[A-Za-z0-9_.+-]+', metadata['version'])):
            raise ValueError('Duplicate package or invalid version')
        extras = '[rsa]' if name == 'pymysql' else ''
        rows[name] = f'{name}{extras}=={metadata["version"]} --hash=sha256:{digest}'
    if RUNTIME - rows.keys():
        raise ValueError('Report omits required runtime packages; use --ignore-installed with the full profile inputs')
    expected = '2.7.1+cpu' if profile == 'cpu' else '2.7.1+cu118'
    if not rows.get('torch', '').startswith(f'torch=={expected} '):
        raise ValueError('The resolved PyTorch build does not match the selected profile')
    if profile == 'cpu' and any(name.startswith('nvidia-') or name in {'accelerate', 'bitsandbytes', 'triton', 'torchaudio'}
                                for name in rows):
        raise ValueError('CPU locks must not contain CUDA/quantization/audio companion packages')
    if profile == 'cu118':
        if not rows.get('torchaudio', '').startswith('torchaudio==2.7.1+cu118 '):
            raise ValueError('TorchAudio must match the CUDA PyTorch build')
        if not rows.get('bitsandbytes', '').startswith('bitsandbytes==0.47.0 ') or 'accelerate' not in rows:
            raise ValueError('Keep the tested older-GPU quantization stack')
    return '\n'.join(rows[name] for name in sorted(rows)) + '\n'


def install(profile, wheelhouses):
    target = native_target()
    existing = {canonical(distribution.metadata['Name']) for distribution in importlib.metadata.distributions()}
    if existing - BOOTSTRAP - {'setuptools'}:
        raise RuntimeError('Install into a new virtual environment, not the developer/application environment')
    if sys.prefix == sys.base_prefix:
        raise RuntimeError('Create a virtual environment before installing a profile')
    lock = LOCKS / f'{profile}-{target}.txt'
    read_lock(lock)
    command = [sys.executable, '-m', 'pip', '--isolated', 'install', '--require-hashes',
               '--only-binary=:all:', '-r', str(lock)]
    if wheelhouses:
        command.append('--no-index')
        for directory in wheelhouses:
            if not directory.is_dir():
                raise ValueError(f'Wheelhouse does not exist: {directory}')
            command.extend(('--find-links', str(directory.resolve())))
    else:
        command.extend(('--index-url', 'https://pypi.org/simple', '--extra-index-url',
                        f'https://download.pytorch.org/whl/{"cpu" if profile == "cpu" else "cu118"}'))
    subprocess.run(command, check=True)
    subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True)


def reject_network(*args, **kwargs):
    raise RuntimeError('Dependency verification must not access networks or a configured database')


def smoke(profile, software_only):
    with ExitStack() as resources:
        resources.enter_context(patch.dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                                           STT_LOCAL_FILES_ONLY='1'))
        for name in ('create_connection', 'getaddrinfo'):
            resources.enter_context(patch.object(socket, name, reject_network))
        for name in ('connect', 'connect_ex', 'sendto'):
            resources.enter_context(patch.object(socket.socket, name, reject_network))
        import av
        import ctranslate2
        import flask
        import flask_cors
        import flask_sock
        import faster_whisper
        import numpy
        import onnxruntime
        import pymupdf
        import pymysql
        import simple_websocket
        import torch
        from transformers import AutoConfig, AutoModel, AutoModelForCausalLM, AutoTokenizer

        torch.set_num_threads(1)
        result = torch.arange(16, dtype=torch.float32).reshape(4, 4) @ torch.eye(4)
        if not torch.equal(result, torch.arange(16, dtype=torch.float32).reshape(4, 4)):
            raise RuntimeError('CPU tensor operation failed')
        if numpy.frombuffer(numpy.ones(4, dtype=numpy.float32).tobytes(), dtype=numpy.float32).size != 4:
            raise RuntimeError('NumPy binary adapter failed')
        array = numpy.arange(16, dtype=numpy.float32).reshape(4, 4)
        if not numpy.array_equal(torch.from_numpy(array).numpy(), array):
            raise RuntimeError('PyTorch/NumPy binary bridge failed')
        av.CodecContext.create('pcm_s16le', 'r')
        with pymupdf.open() as document:
            document.new_page()
            if not document.tobytes().startswith(b'%PDF'):
                raise RuntimeError('PDF binary adapter failed')
        if 'int8' not in ctranslate2.get_supported_compute_types('cpu'):
            raise RuntimeError('CPU/int8 speech backend unavailable')
        for model_type in ('qwen3', 'qwen3_5_text', 'gemma4_text'):
            config = AutoConfig.for_model(model_type)
            AutoModelForCausalLM._model_mapping[type(config)]
        if profile == 'cpu':
            if torch.version.cuda is not None:
                raise RuntimeError('CPU profile unexpectedly contains a CUDA PyTorch build')
        else:
            import accelerate
            import bitsandbytes
            import torchaudio

            if torch.version.cuda != '11.8':
                raise RuntimeError('The older-GPU profile requires CUDA 11.8')
            if software_only:
                print('SOFTWARE ONLY: CUDA imports passed; GPU execution/driver/quantization are NOT verified.')
            else:
                if not torch.cuda.is_available():
                    raise RuntimeError('CUDA hardware verification failed; no CPU fallback is accepted')
                layer = bitsandbytes.nn.Linear4bit(64, 32, bias=False, compute_dtype=torch.float16,
                                                  quant_type='nf4', compress_statistics=True).to('cuda')
                output = layer(torch.ones((4, 64), device='cuda', dtype=torch.float16))
                torch.cuda.synchronize()
                if output.device.type != 'cuda' or output.shape != (4, 32) or not torch.isfinite(output).all().item():
                    raise RuntimeError('CUDA FP16/NF4 operation failed')
                print(f'GPU: {torch.cuda.get_device_name(0)}; capability={torch.cuda.get_device_capability(0)}')
        print(f'Binary imports, model-class compatibility and {profile} tensor smoke passed; no models/DB loaded.')


def verify(profile, software_only):
    expected = read_lock(LOCKS / f'{profile}-{native_target()}.txt')
    installed = {canonical(distribution.metadata['Name']): distribution.version
                 for distribution in importlib.metadata.distributions()}
    mismatch = {name: (version, installed.get(name)) for name, version in expected.items()
                if installed.get(name) != version}
    unexpected = sorted(set(installed) - set(expected) - BOOTSTRAP)
    if mismatch or unexpected:
        raise RuntimeError(f'Environment differs from lock: mismatched={mismatch}; extra={unexpected}')
    subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True)
    smoke(profile, software_only)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('install', 'verify', 'record'):
        command = commands.add_parser(name)
        command.add_argument('--profile', choices=('cpu', 'cu118'), required=True)
        if name == 'install':
            command.add_argument('--wheelhouse', type=Path, action='append', default=[])
        elif name == 'verify':
            command.add_argument('--software-only', action='store_true',
                                 help='CUDA imports only; does not certify a GPU/driver')
        else:
            command.add_argument('--report', type=Path, required=True)
            command.add_argument('--target', choices=tuple(TARGETS), required=True)
            command.add_argument('--output', type=Path, required=True)
    options = parser.parse_args(argv)
    try:
        if options.command == 'install':
            install(options.profile, options.wheelhouse)
        elif options.command == 'verify':
            verify(options.profile, options.software_only)
        else:
            report = json.loads(options.report.read_text(encoding='utf-8'))
            contents = record_lock(report, options.profile, options.target)
            options.output.write_text(contents, encoding='utf-8', newline='\n')
        return 0
    except (OSError, ImportError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Dependency profile failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
