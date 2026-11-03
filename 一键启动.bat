@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

where pyw.exe >nul 2>nul
if not errorlevel 1 goto launch_pyw

where pythonw.exe >nul 2>nul
if not errorlevel 1 goto launch_pythonw

echo.
echo 未检测到可用的 Python 窗口环境。
echo Python with Tk support was not found.
echo 请先安装 Python 3，并在安装时勾选 Add Python to PATH。
echo Install Python 3 and enable Add Python to PATH.
echo.
pause
exit /b 1

:launch_pyw
start "" pyw.exe -3 -m buaatd gui
exit /b 0

:launch_pythonw
start "" pythonw.exe -m buaatd gui
exit /b 0
