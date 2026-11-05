@echo off
setlocal
set "PYTHON=%~dp0.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Create .venv with CPython 3.13 x64, then run its Python with scripts\dependencies.py install --profile cu118.
    exit /b 1
)

"%PYTHON%" -c "import torch; assert torch.cuda.is_available(), f'CUDA unavailable: torch={torch.__version__}, CUDA={torch.version.cuda}. Recreate the locked cu118 profile using scripts/dependencies.py and check the NVIDIA driver.'; print('GPU:', torch.cuda.get_device_name(0))"
if errorlevel 1 exit /b %errorlevel%

set "GEN_DEVICE=cuda"
"%PYTHON%" "%~dp0backend\main.py"
exit /b %errorlevel%
