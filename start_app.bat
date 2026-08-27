@echo off
rem ph-auto-label 一键启动（双击运行）
rem 绑定 0.0.0.0：本机 127.0.0.1:5000 和局域网 172.16.9.234:5000 都能访问
cd /d "%~dp0"
set FLASK_HOST=0.0.0.0
python run.py
pause
