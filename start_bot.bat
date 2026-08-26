@echo off
chcp 65001 >nul
title Сова - Telegram Bot

:loop
echo [%date% %time%] Запуск бота Сова...
cd /d "D:\БОТ СОВА для ТГ"
"C:\Users\user\AppData\Local\Programs\Python\Python312\python.exe" bot.py
echo [%date% %time%] Бот остановился. Перезапуск через 5 секунд...
timeout /t 5 /nobreak >nul
goto loop
