@echo off
rem ============================================================
rem  update_slim_publish_hf.bat -- 更新 space_db_slim.duckdb 並發布（Dataset 架構）
rem
rem  2026-09-07 起 DB 已移出 Space git（根本解）：
rem    發布＝上傳 HF Dataset（RhynoWu/satdashboard-db）＋壓縮歷史＋重啟 Space；
rem    Space 容器開機時由 resolve_db() 自動下載 slim DB。
rem    不再有 git LFS push／prune／歷史重寫／fetch 復原等機關。
rem
rem  用法：update_slim_publish_hf.bat [dryrun]
rem    dryrun = 只做 1-3 步（重建/併入/部署副本），不上傳不重啟
rem
rem  注意：本檔以 CP950+CRLF 儲存（中文 UTF-8 bat 會被 cmd 錯位執行）。
rem ============================================================
setlocal
set PYTHONIOENCODING=utf-8
set APP=F:\GitHub\Sat_TraingDataExtension\scenario-advanced01
set PARENT=F:\GitHub\Sat_TraingDataExtension

echo [%TIME:~0,8%] [1/4] 重建頂層 slim DB（近 14 天 + 白名單全歷史，約 15-60 秒）...
cd /d %PARENT%
python prc_maneuver\build_slim_db.py --slim-only --keep-lines --recent-days 14
if errorlevel 1 goto :fail

echo [%TIME:~0,8%] [2/4] 複製到 app 本機 DB 並併入 StoryMap TLE（約 10-30 秒）...
copy /y %PARENT%\space_db_slim.duckdb %APP%\DB\space_db_slim.duckdb >nul
if errorlevel 1 goto :fail
cd /d %APP%
python tools\merge_storymap_tle.py
if errorlevel 1 goto :fail

echo [%TIME:~0,8%] [3/4] 複製到部署副本 scenario04\DB（本機 run.py 用）...
copy /y %APP%\DB\space_db_slim.duckdb %APP%\scenario04\DB\space_db_slim.duckdb >nul
if errorlevel 1 goto :fail
for %%A in (%APP%\scenario04\DB\space_db_slim.duckdb) do set /a DBMB=%%~zA/1048576
echo            部署副本就緒（%DBMB% MB）

if /i "%~1"=="dryrun" (
  echo [%TIME:~0,8%] [4/4] dryrun：略過 Dataset 上傳與 Space 重啟。
  goto :ok
)

echo [%TIME:~0,8%] [4/4] 上傳 %DBMB% MB 至 HF Dataset ＋ 壓縮歷史 ＋ 重啟 Space（約 1-10 分鐘）...
python tools\publish_db_to_dataset.py
if errorlevel 1 goto :fail

:ok
curl -s https://huggingface.co/api/spaces/RhynoWu/ATRDC-SatDashboard | python -c "import sys,json;d=json.load(sys.stdin);print('HF sha',(d.get('sha') or '')[:8],'stage',d.get('runtime',{}).get('stage'))"
echo [%TIME:~0,8%] [OK] 完成（Space 重啟後約 1-3 分鐘恢復 RUNNING 並載入新 DB）
exit /b 0
:fail
echo [FAIL] 失敗（errorlevel %errorlevel%），流程中止
exit /b 1
