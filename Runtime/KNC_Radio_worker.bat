@echo off
set "FFMPEG=C:\ffmpeg\bin\ffmpeg.exe"
set "STREAM=%~1"
set "OUTDIR=C:\Users\HP\OneDrive\Desktop\Test\RadioRecordings\KNC_Radio"
set "LOGFILE=C:\Users\HP\OneDrive\Desktop\Test\Runtime\KNC_Radio.log"
set "PIDFILE=C:\Users\HP\OneDrive\Desktop\Test\Runtime\KNC_Radio.pid"
set "OUTPATTERN=%OUTDIR%\%%Y\%%m\%%d\%%Y-%%m-%%d-%%H-%%M-%%S.mp3"
if "%STREAM%"=="" exit /b 1
if not exist "%OUTDIR%" mkdir "%OUTDIR%"
powershell -NoProfile -Command "$o=$env:OUTDIR; foreach($i in -1..120){ $d=(Get-Date).AddDays($i).ToString('yyyy\\MM\\dd'); $null=[System.IO.Directory]::CreateDirectory((Join-Path $o $d)) }" >nul 2>nul
"%FFMPEG%" -hide_banner -nostdin -loglevel warning -fflags +discardcorrupt -err_detect ignore_err -reconnect 1 -reconnect_streamed 1 -reconnect_at_eof 1 -reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx -reconnect_delay_max 15 -rw_timeout 15000000 -i "%STREAM%" -vn -acodec libmp3lame -b:a 96k -ar 44100 -ac 2 -f segment -segment_time 3600 -segment_atclocktime 1 -strftime 1 "%OUTPATTERN%" >>"%LOGFILE%" 2>&1
