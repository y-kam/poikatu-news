@echo off
chcp 65001 >nul
rem ちょびリッチをこのPC（自宅回線）から取得してサイトへ反映する（ダブルクリック実行用）。
rem
rem ちょびリッチはGitHub Actions・レンタルサーバ等のデータセンター系IPをWAFで
rem 恒久ブロックしており（2026-07-16〜）、CIからは取得できない。そのためCIの
rem クロールからは除外し（crawl.yml の --exclude chobirich）、このスクリプトで
rem ローカル取得→データpushする。push後は deploy ワークフローが自動発火して
rem 数分でサイトに反映される。実行頻度は任意（数日空いても他サイトに影響なし）。
cd /d "%~dp0.."

echo [1/3] リモートの最新データを取り込みます...
git pull --rebase origin main || goto :fail

echo [2/3] ちょびリッチをクロールします（1分ほどかかります）...
python run.py --sites chobirich || goto :fail

echo [3/3] データをコミットしてpushします...
git add data/
git diff --cached --quiet && echo 新しいデータはありませんでした（push不要）。 && goto :done
git commit -m "data: chobirich local crawl" || goto :fail
git pull --rebase origin main || goto :fail
git push origin main || goto :fail
echo push しました。数分でサイトに反映されます。

:done
echo 完了しました。
pause
exit /b 0

:fail
echo 失敗しました。上のエラーメッセージを確認してください。
pause
exit /b 1
