@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

if "%PORT%"=="" set "PORT=8000"

where python >nul 2>nul
if errorlevel 1 (
  echo [x] 没有找到 python，请先安装 Python 3.9+ 并勾选 "Add to PATH"
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] 创建虚拟环境 .venv
  python -m venv .venv
  if errorlevel 1 (
    echo [x] 虚拟环境创建失败
    pause
    exit /b 1
  )
)

echo [2/4] 安装后端依赖（首次会联网下载）
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
".venv\Scripts\python.exe" -m pip install -q -r requirements-optional.txt
if errorlevel 1 (
  echo     [!] ViennaRNA 未安装，后端自动降级为纯 Python 引擎（功能可用，能量为估计值）
)

if not exist "frontend\dist\index.html" (
  where npm >nul 2>nul
  if errorlevel 1 (
    echo [!] 没检测到 npm，跳过前端构建。装好 Node.js 18+ 后重跑本脚本；
    echo     现在也可以直接看 API 文档 http://127.0.0.1:%PORT%/docs
  ) else (
    echo [3/4] 首次运行，构建前端（约 1-2 分钟）
    pushd frontend
    call npm install --no-fund --no-audit
    call npm run build
    popd
  )
) else (
  echo [3/4] 前端产物已存在，跳过构建
)

echo [4/4] 启动服务：http://127.0.0.1:%PORT%  （关闭本窗口即停止）
start "" http://127.0.0.1:%PORT%
".venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port %PORT%
pause
