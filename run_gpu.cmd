@echo off
setlocal
set "PYTHON=%~dp0.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Create .venv with python -m venv .venv, then install requirements-cuda.txt using its Python.
    exit /b 1
)

"%PYTHON%" -c "import torch; assert torch.cuda.is_available(), f'CUDA unavailable: torch={torch.__version__}, CUDA={torch.version.cuda}. Install requirements-cuda.txt and check the NVIDIA driver.'; print('GPU:', torch.cuda.get_device_name(0))"
if errorlevel 1 exit /b %errorlevel%

set "GEN_DEVICE=cuda"
"%PYTHON%" "%~dp0backend\main.py"
exit /b %errorlevel%
