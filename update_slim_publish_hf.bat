@echo off
rem ============================================================
rem  update_slim_publish_hf.bat -- 更新 space_db_slim.duckdb 並發布到 HF Dataset 架構
rem
rem  2026-09-07 起 DB 已移出 Space git：
rem    發布＝上傳 HF Dataset RhynoWu/satdashboard-db ＋壓縮歷史＋重啟兩個 Space；
rem    Space 容器開機時由 resolve_db 自動下載 slim DB。
rem
rem  用法：update_slim_publish_hf.bat [dryrun] [skipbuild]
rem    dryrun    = 只做本機步驟，不上傳、不重啟
rem    skipbuild = 略過第 1 步，沿用頂層既有 slim DB；download_tle_unified.bat publish 會帶此參數
rem    nofetch   = skip step 0 (daily Space-Track TLE fetch into master DB)
rem
rem  注意：本檔以 CP950+CRLF 儲存。echo 行一律只用 ASCII：
rem    Big5 全形括號「）」的第二個位元組是 0x5E，也就是 cmd 的跳脫字元 ^，
rem    放在 echo 行尾會把下一行接進 echo，2026-09-23 曾因此讓 if 判斷被吃掉、誤走 dryrun。
rem ============================================================
setlocal
set PYTHONIOENCODING=utf-8
set APP=%~dp0
set APP=%APP:~0,-1%
for %%I in ("%APP%\..") do set PARENT=%%~fI
set DRYRUN=0
set SKIPBUILD=0
set NOFETCH=0
for %%A in (%*) do (
  if /i "%%~A"=="dryrun" set DRYRUN=1
  if /i "%%~A"=="skipbuild" set SKIPBUILD=1
  if /i "%%~A"=="nofetch" set NOFETCH=1
)

rem [0/4] daily incremental TLE fetch (Space-Track -> master space_db.duckdb)
rem       skipped by skipbuild (caller already fetched) or nofetch
if "%SKIPBUILD%"=="1" goto :skipfetch
if "%NOFETCH%"=="1" goto :skipfetch
echo [%TIME:~0,8%] [0/4] Daily incremental TLE fetch from Space-Track ...
cd /d "%PARENT%"
python download_TLE_unified.py --mode spacetrack
if errorlevel 1 goto :fail
:skipfetch
if "%SKIPBUILD%"=="1" (
  echo [%TIME:~0,8%] [1/4] skipbuild - reuse existing top-level slim DB
  goto :merge
)
echo [%TIME:~0,8%] [1/4] Rebuild top-level slim DB - recent 14 days plus whitelist history, 15-60 s ...
cd /d "%PARENT%"
python prc_maneuver\build_slim_db.py --slim-only --keep-lines --recent-days 14
if errorlevel 1 goto :fail

:merge
echo [%TIME:~0,8%] [2/4] Copy to app DB and merge StoryMap TLE, 10-30 s ...
if not exist "%PARENT%\space_db_slim.duckdb" (
  echo [FAIL] top-level slim DB not found
  goto :fail
)
copy /y "%PARENT%\space_db_slim.duckdb" "%APP%\DB\space_db_slim.duckdb" >nul
if errorlevel 1 goto :fail
cd /d "%APP%"
python tools\merge_storymap_tle.py
if errorlevel 1 goto :fail

echo [%TIME:~0,8%] [3/4] Copy to deploy copy scenario04\DB for local run.py ...
copy /y "%APP%\DB\space_db_slim.duckdb" "%APP%\scenario04\DB\space_db_slim.duckdb" >nul
if errorlevel 1 goto :fail
for %%A in ("%APP%\scenario04\DB\space_db_slim.duckdb") do set /a DBMB=%%~zA/1048576
echo            deploy copy ready: %DBMB% MB

if "%DRYRUN%"=="1" (
  echo [%TIME:~0,8%] [4/4] dryrun - skip Dataset upload and Space restart
  goto :ok
)
echo [%TIME:~0,8%] [4/4] Upload %DBMB% MB to HF Dataset, squash history, restart both Spaces, 1-10 min ...
python tools\publish_db_to_dataset.py
if errorlevel 1 goto :fail

:ok
python -c "import json,urllib.request as u;[print(s,'sha',(d.get('sha') or '')[:8],'stage',d.get('stage')) for s in ('RhynoWu/ATRDC-SatDashboard','RhynoWu/ATRDC-SatDashboard-i18n') for d in [json.load(u.urlopen('https://huggingface.co/api/spaces/'+s+'/runtime',timeout=30))]]"
echo [%TIME:~0,8%] [OK] done - Spaces return to RUNNING with the new DB in about 1-3 min
exit /b 0
:fail
echo [FAIL] errorlevel %errorlevel% - aborted
exit /b 1
