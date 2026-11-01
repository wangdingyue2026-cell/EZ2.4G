@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
rem 优先用 PATH 里的 pythonw，其次用 Python 启动器 pyw
where pythonw >nul 2>nul && (start "" pythonw "%~dp0my_devices.py" & exit /b)
where pyw >nul 2>nul && (start "" pyw -3 "%~dp0my_devices.py" & exit /b)
echo 没有找到 Python。请安装 Python 3.10+（安装时勾选 Add python.exe to PATH），或直接使用 2.4G.exe。
pause
