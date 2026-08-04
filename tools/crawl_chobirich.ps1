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
$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:TempWorktree = $null
Set-Location $script:RepoRoot

# 普段の作業ツリーに未コミット変更があっても触れないよう、クロールは一時worktreeで行う。
function Remove-TempWorktree {
    if (-not $script:TempWorktree) { return }

    Set-Location $script:RepoRoot
    git worktree remove --force $script:TempWorktree 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "警告: 一時worktreeを自動削除できませんでした: $script:TempWorktree"
    }
    $script:TempWorktree = $null
}

# 終了処理: 失敗メッセージ表示と、ウィンドウが即閉じないためのキー待ち
function Finish([int]$code) {
    Remove-TempWorktree
    if ($code -ne 0) { Write-Host "失敗しました。上のエラーメッセージを確認してください。" }
    if (-not $NoPause) { [void](Read-Host "Enterキーを押すと閉じます") }
    exit $code
}

Write-Host "[1/3] リモートの最新データを取り込みます..."
git fetch origin main
if ($LASTEXITCODE -ne 0) { Finish 1 }

$script:TempWorktree = Join-Path ([System.IO.Path]::GetTempPath()) (
    "poikatu-chobirich-" + [Guid]::NewGuid().ToString("N")
)
git worktree add --detach $script:TempWorktree origin/main
if ($LASTEXITCODE -ne 0) { Finish 1 }
Set-Location $script:TempWorktree

Write-Host "[2/3] ちょびリッチをクロールします（数分かかります）..."
python run.py --sites chobirich
if ($LASTEXITCODE -ne 0) { Finish 1 }

Write-Host "[3/3] データをコミットしてpushします..."
# クロールが更新するファイルだけを対象にし、別作業のdataファイルを混ぜない。
git add -- data/deals.json data/crawl_metrics.json data/history.json
if ($LASTEXITCODE -ne 0) { Finish 1 }
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "新しいデータはありませんでした（push不要）。完了しました。"
    Finish 0
}
git commit -m "data: chobirich local crawl"
if ($LASTEXITCODE -ne 0) { Finish 1 }
git fetch origin main
if ($LASTEXITCODE -ne 0) { Finish 1 }
git rebase origin/main
if ($LASTEXITCODE -ne 0) { Finish 1 }

# push の認証: GCM（Windows資格情報マネージャー）の保存エントリが壊れており
# パスワード入力に落ちて必ず失敗するため、gh CLI の保存トークンでpushする。
# gh はアクティブアカウントのトークンしか返さないので、pushの間だけ y-kam に
# 切り替え、終わったら通常運用の ykameyama に戻す（gh併存運用の自動化）。
gh auth switch -u y-kam 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "gh のアカウント切替（y-kam）に失敗しました。"; Finish 1 }

# 送り先の指定は必ず HEAD:main とする。この一時worktreeはdetached HEADなので、
# refspecに "main" と書くと（worktree間で共有される）ローカルの refs/heads/main＝
# 普段の作業ツリーのmainが送られる。CIが日に数回pushする運用ではローカルmainは
# 常にremoteより遅れているため、"tip is behind its remote counterpart" で毎回失敗する。
# また送信中にCI側のpushでremoteが進むこともあるため、拒否されたら取り込み直して再試行する。
$pushExit = 1
foreach ($attempt in 1..3) {
    git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push origin HEAD:main
    $pushExit = $LASTEXITCODE
    if ($pushExit -eq 0) { break }
    if ($attempt -eq 3) { break }

    Write-Host "pushが拒否されました。リモートの最新を取り込んで再試行します（$attempt/3）..."
    git fetch origin main
    if ($LASTEXITCODE -ne 0) { break }
    git rebase origin/main
    if ($LASTEXITCODE -ne 0) { git rebase --abort 2>&1 | Out-Null; break }
}
gh auth switch -u ykameyama 2>&1 | Out-Null
if ($pushExit -ne 0) { Finish 1 }
Write-Host "push しました。数分でサイトに反映されます。完了しました。"
Finish 0
