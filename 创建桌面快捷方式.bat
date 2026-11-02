@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "BUAATD_APP_DIR=%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$shell = New-Object -ComObject WScript.Shell; $desktop = [Environment]::GetFolderPath('Desktop'); $shortcut = $shell.CreateShortcut((Join-Path $desktop '北航TD控制台.lnk')); $shortcut.TargetPath = (Join-Path $env:BUAATD_APP_DIR '一键启动.bat'); $shortcut.WorkingDirectory = $env:BUAATD_APP_DIR; $shortcut.IconLocation = '%SystemRoot%\System32\shell32.dll,21'; $shortcut.Description = '北航 TD 自动运行控制台'; $shortcut.Save()"

if errorlevel 1 (
    echo.
    echo 创建快捷方式失败。
    pause
    exit /b 1
)

echo.
echo 已在桌面创建“北航TD控制台”快捷方式。
timeout /t 2 >nul
exit /b 0
