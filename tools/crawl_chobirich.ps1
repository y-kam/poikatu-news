# ちょびリッチをこのPC（自宅回線）から取得してサイトへ反映する。
# 起動は同フォルダの crawl_chobirich.bat（ダブルクリック）から。
#
# ちょびリッチはGitHub Actions・レンタルサーバ等のデータセンター系IPをWAFで
# 恒久ブロックしており（2026-07-16〜）、CIからは取得できない。そのためCIの
# クロールからは除外し（crawl.yml の --exclude chobirich）、このスクリプトで
# ローカル取得→データpushする。push後は deploy ワークフローが自動発火して
# 数分でサイトに反映される。実行頻度は任意（数日空いても他サイトに影響なし）。
#
# ※.batに日本語を書くと cmd の chcp 65001 バグ（読み取り位置ずれ）で誤動作する
#   ため、処理と日本語メッセージは本ファイル（PowerShell）に置いている。
param([switch]$NoPause)  # 自動実行（タスクスケジューラ等）用: 最後のキー待ちを省く

chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location (Join-Path $PSScriptRoot "..")

# 終了処理: 失敗メッセージ表示と、ウィンドウが即閉じないためのキー待ち
function Finish([int]$code) {
    if ($code -ne 0) { Write-Host "失敗しました。上のエラーメッセージを確認してください。" }
    if (-not $NoPause) { [void](Read-Host "Enterキーを押すと閉じます") }
    exit $code
}

Write-Host "[1/3] リモートの最新データを取り込みます..."
git pull --rebase origin main
if ($LASTEXITCODE -ne 0) { Finish 1 }

Write-Host "[2/3] ちょびリッチをクロールします（数分かかります）..."
python run.py --sites chobirich
if ($LASTEXITCODE -ne 0) { Finish 1 }

Write-Host "[3/3] データをコミットしてpushします..."
git add data/
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "新しいデータはありませんでした（push不要）。完了しました。"
    Finish 0
}
git commit -m "data: chobirich local crawl"
if ($LASTEXITCODE -ne 0) { Finish 1 }
git pull --rebase origin main
if ($LASTEXITCODE -ne 0) { Finish 1 }

# push の認証: GCM（Windows資格情報マネージャー）の保存エントリが壊れており
# パスワード入力に落ちて必ず失敗するため、gh CLI の保存トークンでpushする。
# gh はアクティブアカウントのトークンしか返さないので、pushの間だけ y-kam に
# 切り替え、終わったら通常運用の ykameyama に戻す（gh併存運用の自動化）。
gh auth switch -u y-kam 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "gh のアカウント切替（y-kam）に失敗しました。"; Finish 1 }
git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push origin main
$pushExit = $LASTEXITCODE
gh auth switch -u ykameyama 2>&1 | Out-Null
if ($pushExit -ne 0) { Finish 1 }
Write-Host "push しました。数分でサイトに反映されます。完了しました。"
Finish 0
