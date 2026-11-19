@echo off
setlocal EnableExtensions
chcp 936 >nul 2>&1
title 床旁超声心动图实时全心功能评估系统 · 2026pld

set "HERE=%~dp0"
set "UI=%HERE%07_ui"
set "APP=%UI%\workstation.py"

echo ==========================================================
echo    床旁超声心动图实时全心功能评估系统   (2026pld)
echo    板卡 192.168.1.254 : 5060
echo    三种模式：快照 / 关键帧 / 现场
echo ==========================================================
echo.

if not exist "%APP%" goto no_app

rem ---------- 1. 选 Python：优先 srtp-vision 环境，其次 PATH 上的 python ----------
set "PY="
set "PYW="
if exist "%USERPROFILE%\anaconda3\envs\srtp-vision\python.exe" (
    set "PY=%USERPROFILE%\anaconda3\envs\srtp-vision\python.exe"
    set "PYW=%USERPROFILE%\anaconda3\envs\srtp-vision\pythonw.exe"
)
if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 (
        set "PY=python"
        set "PYW=pythonw"
    )
)
if not defined PY goto no_py

rem ---------- 2. 依赖：numpy / scipy / opencv / Pillow ----------
"%PY%" -c "import numpy, scipy, cv2, PIL" >nul 2>&1
if errorlevel 1 goto no_dep
if /I "%~1"=="--check" (
    rem --check: dependency + board self-check, no GUI (check.py)
    "%PY%" "%UI%\check.py" %2 %3
    pause
    exit /b 0
)

rem ---------- 3. 参数：不给就用默认板卡地址 ----------
set "ARGS=%*"
if "%ARGS%"=="" set "ARGS=192.168.1.254 5060"
echo [启动] Python : %PY%
echo [启动] 参数   : %ARGS%
echo.

rem --console：留在当前窗口（调试用）
echo %ARGS% | findstr /I /C:"--console" >nul
if not errorlevel 1 (
    "%PY%" "%APP%" %ARGS%
    echo.
    echo [已退出] 按任意键关闭窗口。
    pause >nul
    exit /b 0
)

start "" "%PYW%" "%APP%" %ARGS%
echo 已在后台启动，关掉本窗口不影响上位机。
echo 提示：加 --console 参数可留在窗口里看日志；加 --mode live 可直接进现场模式。
timeout /t 3 >nul
exit /b 0

:no_app
echo [错误] 找不到 %APP%
echo        请把本脚本和 07_ui 目录放在同一层再运行。
echo.
pause
exit /b 1

:no_py
echo [错误] 没找到 Python。
echo        装一个 Python 3.9 以上（安装时勾选 Add to PATH），或者把
echo        srtp-vision 环境放到 %USERPROFILE%\anaconda3\envs\srtp-vision
echo.
pause
exit /b 1

:no_dep
echo [提示] 这个 Python 缺少上位机依赖（numpy / scipy / opencv / Pillow）。
echo        装一次就好：
echo          "%PY%" -m pip install numpy scipy opencv-contrib-python pillow
echo.
pause
exit /b 1
