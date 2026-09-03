@echo off
setlocal
set PYTHONIOENCODING=utf-8
rem ============================================================
rem  update_slim_publish_hf.bat — 更新 space_db_slim.duckdb 並發布至 HF Space
rem
rem  流程（2026-09-02 實證）：
rem    1) 父專案 build_slim_db.py：全衛星近 14 天 + 白名單全歷史（含 TJS-10/TJS-3）
rem    2) 複製到 scenario-advanced01\DB\（run.py 本機優先）
rem    3) merge_storymap_tle.py 併入 StoryMap 所需 ~1.3 萬顆歷史
rem    4) 複製到 scenario04\DB\（部署副本）-> commit -> push hf master:main
rem
rem  用法：update_slim_publish_hf.bat [prune]
rem    prune = 推送前先永久刪除 HF 上舊版 slim DB 的 LFS blob（保留最新一版）。
rem            HF Space 儲存上限 1 GB，每版 ~150 MB，約每 5 版需清一次。
rem            注意：刪除會使 HF 端歷史被重寫（SHA 變動），詳見 README。
rem ============================================================
set APP=F:\GitHub\Sat_TraingDataExtension\scenario-advanced01
set PARENT=F:\GitHub\Sat_TraingDataExtension

echo [1/5] 重建頂層 slim DB（近 14 天 + 白名單全歷史）...
cd /d %PARENT%
python prc_maneuver\build_slim_db.py --slim-only --keep-lines --recent-days 14
if errorlevel 1 goto :fail

echo [2/5] 複製到 app 本機 DB 並併入 StoryMap TLE ...
copy /y %PARENT%\space_db_slim.duckdb %APP%\DB\space_db_slim.duckdb >nul
if errorlevel 1 goto :fail
cd /d %APP%
python tools\merge_storymap_tle.py
if errorlevel 1 goto :fail

echo [3/5] 複製到部署副本 scenario04\DB ...
copy /y %APP%\DB\space_db_slim.duckdb %APP%\scenario04\DB\space_db_slim.duckdb >nul
if errorlevel 1 goto :fail

if /i "%~1"=="prune" (
  echo [4/5] 清理 HF 舊版 slim DB LFS blob（保留最新一版）...
  python tools\prune_hf_slim_lfs.py
  if errorlevel 1 goto :fail
) else (
  echo [4/5] 略過 LFS 清理（需要時：update_slim_publish_hf.bat prune）
)

echo [5/5] commit 並推送 HF ...
git add -f scenario04\DB\space_db_slim.duckdb
git diff --cached --quiet && echo   DB 無變更，跳過 commit/push。 && goto :ok
for /f %%d in ('python -c "import datetime;print(datetime.date.today())"') do set TODAY=%%d
git commit -m "data: daily slim DB update (%TODAY%, build_slim+merge_storymap pipeline)"
if errorlevel 1 goto :fail
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
git push origin master
if errorlevel 1 (
  echo   origin 非快轉（HF 歷史重寫所致），以 force-with-lease 對齊 ...
  git push --force-with-lease origin master
  if errorlevel 1 echo   [!] origin 推送失敗（不影響 HF 部署），請手動檢查。
)

:ok
curl -s https://huggingface.co/api/spaces/RhynoWu/ATRDC-SatDashboard | python -c "import sys,json;d=json.load(sys.stdin);print('HF sha',(d.get('sha') or '')[:8],'stage',d.get('runtime',{}).get('stage'))"
echo [OK] 完成
exit /b 0
:fail
echo [FAIL] 失敗（errorlevel %errorlevel%），流程中止
exit /b 1
