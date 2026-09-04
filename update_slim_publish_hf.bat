@echo off
rem ============================================================
rem  update_slim_publish_hf.bat -- 更新 space_db_slim.duckdb 並發布至 HF Space
rem
rem  流程：
rem    1) 父專案 build_slim_db.py：全衛星近 14 天 + 白名單全歷史（含 TJS-10/TJS-3）
rem    2) 複製到 scenario-advanced01\DB\（run.py 本機優先）並併入 StoryMap TLE
rem    3) 複製到 scenario04\DB\（部署副本）
rem    4) （prune 時）清理 HF 舊版 slim DB LFS blob + 孤兒 blob
rem    5) commit -> push hf master:main -> push origin
rem
rem  用法：update_slim_publish_hf.bat [prune | dryrun]
rem    prune  = 推送前清理 HF LFS（每版 ~150-200 MB、上限 1 GB，約每 4 版清一次；
rem             清理會重寫 HF 端歷史，push 遇 fetch-first 會自動復原）
rem    dryrun = 只做 1-3 步（重建/併入/部署副本），不 commit 不 push，供測試
rem
rem  注意：本檔必須以 CP950+CRLF 儲存（中文 UTF-8 bat 會被 cmd 錯位執行）；
rem        內含之 git commit 訊息一律 ASCII（CP950 中文訊息入庫會亂碼）。
rem ============================================================
setlocal
set PYTHONIOENCODING=utf-8
set APP=F:\GitHub\Sat_TraingDataExtension\scenario-advanced01
set PARENT=F:\GitHub\Sat_TraingDataExtension

echo [%TIME:~0,8%] [1/5] 重建頂層 slim DB（近 14 天 + 白名單全歷史，約 15-60 秒）...
cd /d %PARENT%
python prc_maneuver\build_slim_db.py --slim-only --keep-lines --recent-days 14
if errorlevel 1 goto :fail

echo [%TIME:~0,8%] [2/5] 複製到 app 本機 DB 並併入 StoryMap TLE（約 10-30 秒）...
copy /y %PARENT%\space_db_slim.duckdb %APP%\DB\space_db_slim.duckdb >nul
if errorlevel 1 goto :fail
cd /d %APP%
python tools\merge_storymap_tle.py
if errorlevel 1 goto :fail

echo [%TIME:~0,8%] [3/5] 複製到部署副本 scenario04\DB ...
copy /y %APP%\DB\space_db_slim.duckdb %APP%\scenario04\DB\space_db_slim.duckdb >nul
if errorlevel 1 goto :fail
for %%A in (%APP%\scenario04\DB\space_db_slim.duckdb) do set /a DBMB=%%~zA/1048576
echo            部署副本就緒（%DBMB% MB）

if /i "%~1"=="dryrun" (
  echo [%TIME:~0,8%] [4/5][5/5] dryrun：略過 LFS 清理與 commit/push。
  goto :ok
)

if /i "%~1"=="prune" (
  echo [%TIME:~0,8%] [4/5] 清理 HF 舊版 slim DB LFS blob（含孤兒，約 10-30 秒）...
  python tools\prune_hf_slim_lfs.py
  if errorlevel 1 goto :fail
) else (
  echo [%TIME:~0,8%] [4/5] 略過 LFS 清理（需要時：update_slim_publish_hf.bat prune）
)

echo [%TIME:~0,8%] [5/5] commit 並推送 HF ...
echo            staging %DBMB% MB（git-lfs 雜湊，此段畫面無輸出，約 10-60 秒，請勿中斷）...
git add -f scenario04\DB\space_db_slim.duckdb
git diff --cached --quiet && echo   DB 無變更，跳過 commit/push。 && goto :ok
for /f %%d in ('python -c "import datetime;print(datetime.date.today())"') do set TODAY=%%d
git commit -m "data: daily slim DB update (%TODAY%, build_slim+merge_storymap pipeline)"
if errorlevel 1 goto :fail
echo [%TIME:~0,8%]            上傳 %DBMB% MB 至 HF（依上行頻寬約 1-10 分鐘，LFS 進度如下）...
git push hf master:main
if errorlevel 1 (
  echo.
  echo   push 失敗；嘗試自動復原（LFS 清理會重寫 HF 端歷史 -> fetch-first 為預期現象）...
  git fetch hf main
  if errorlevel 1 goto :fail
  git branch -f backup_%TODAY% master
  git reset --hard hf/main
  copy /y %APP%\DB\space_db_slim.duckdb %APP%\scenario04\DB\space_db_slim.duckdb >nul
  git add -f scenario04\DB\space_db_slim.duckdb
  git diff --cached --quiet && echo   復原後 DB 無變更。 && goto :origin
  git commit -m "data: daily slim DB update (%TODAY%, build_slim+merge_storymap pipeline)"
  git push hf master:main
  if errorlevel 1 goto :fail
)
:origin
echo [%TIME:~0,8%]            推送 origin（GitHub）...
git push origin master
if errorlevel 1 (
  echo   origin 非快轉（HF 歷史重寫所致），以 force-with-lease 對齊 ...
  git push --force-with-lease origin master
  if errorlevel 1 echo   [!] origin 推送失敗（不影響 HF 部署），請手動檢查。
)

:ok
curl -s https://huggingface.co/api/spaces/RhynoWu/ATRDC-SatDashboard | python -c "import sys,json;d=json.load(sys.stdin);print('HF sha',(d.get('sha') or '')[:8],'stage',d.get('runtime',{}).get('stage'))"
echo [%TIME:~0,8%] [OK] 完成
exit /b 0
:fail
echo [FAIL] 失敗（errorlevel %errorlevel%），流程中止
exit /b 1
