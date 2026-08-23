# CIから取得できないサイトを、このPC（自宅回線）から取得してサイトへ反映する。
# 起動は同フォルダの crawl_local.bat（ダブルクリック）から。個別に走らせたいときは
# crawl_chobirich.bat / crawl_pointincome.bat。
#
# 対象サイトはデータセンター系IP（GitHub Actions・レンタルサーバ）を拒否するため、
# CIのクロール・死活チェックからは除外している（crawl.yml の --exclude）。
#   - ちょびリッチ    : WAFが403を返す（2026-07-16〜）
#   - ポイントインカム: 一覧は200 OKのまま案件0件、詳細ページは掲載中でも dead 判定
#                       （2026-08-22〜ほぼ全滅。死活チェックが掲載中の案件を誤って
#                        掲載終了にしていたため 2026-08-23 にCIから除外した）
# このスクリプトでローカル取得→データpushする。push後は deploy ワークフローが
# 自動発火して数分でサイトに反映される。
#
# 所要: クロール数分＋掲載終了チェック約20分（2サイト並列）。掲載終了チェックが不要な回は
# -SkipLinkCheck を付ける（crawl_local.bat -SkipLinkCheck）。クロール結果は
# 掲載終了チェックの前にpushするので、途中でウィンドウを閉じても取得分は失われない。
#
# 実行頻度: 1日1回程度を目安に（数日空いても他サイトに影響はないが、その間の新着は
# 取りこぼす。掲載終了の確定は連続2〜3回の dead 判定が要るので、間隔が空くぶん遅くなる）。
#
# ※.batに日本語を書くと cmd の chcp 65001 バグ（読み取り位置ずれ）で誤動作するため、
#   処理と日本語メッセージは本ファイル（PowerShell）に置いている。
param(
    [string]$Sites = "chobirich,pointincome",  # 対象サイトキー（カンマ区切り）
    [switch]$SkipLinkCheck,                    # 掲載終了チェック（約20分）を省く
    [switch]$NoPause                           # 自動実行（タスクスケジューラ等）用: 最後のキー待ちを省く
)

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

# data/ の変更をコミットしてpushする。変更が無ければ何もしない。
# 成否は $script:PublishExit（0=成功またはpush不要 / 1=失敗）で返す。
# ※ PowerShellではネイティブコマンド（git）の標準出力も関数の戻り値に混ざるため、
#   return で成否を返すと呼び出し側で数値として判定できない（git出力を含む配列になる）。
function Publish-Data([string]$Message) {
    $script:PublishExit = 1
    # この一時worktreeは毎回 origin/main から作り直す使い捨てなので、data/ 配下の変更は
    # すべてこの実行の成果物。CI（crawl.yml）と同じく data/ をまるごと対象にする。
    # ※ 対象ファイルを列挙していると、週明け最初の生成でだけ書かれる data/weekly.json の
    #   ような「毎回は出ない出力」を取りこぼし、直後の rebase が unstaged changes で失敗する
    #   （＝クロール結果を捨てて終了する）。列挙方式には戻さないこと。
    git add -- data/
    if ($LASTEXITCODE -ne 0) { return }
    git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  新しいデータはありませんでした（push不要）。"
        $script:PublishExit = 0
        return
    }
    git commit -m $Message
    if ($LASTEXITCODE -ne 0) { return }
    git fetch origin main
    if ($LASTEXITCODE -ne 0) { return }
    # --autostash: data/ 以外に想定外の生成物が残っていても rebase を中断させない
    # （使い捨てworktreeなので退避内容は捨てて構わない。中断＝クロール結果消失を避ける）。
    git rebase --autostash origin/main
    if ($LASTEXITCODE -ne 0) { return }

    # push の認証: GCM（Windows資格情報マネージャー）の保存エントリが壊れており
    # パスワード入力に落ちて必ず失敗するため、gh CLI の保存トークンでpushする。
    # gh はアクティブアカウントのトークンしか返さないので、pushの間だけ y-kam に
    # 切り替え、終わったら通常運用の ykameyama に戻す（gh併存運用の自動化）。
    gh auth switch -u y-kam 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "gh のアカウント切替（y-kam）に失敗しました。"; return }

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

        Write-Host "  pushが拒否されました。リモートの最新を取り込んで再試行します（$attempt/3）..."
        git fetch origin main
        if ($LASTEXITCODE -ne 0) { break }
        git rebase --autostash origin/main
        if ($LASTEXITCODE -ne 0) { git rebase --abort 2>&1 | Out-Null; break }
    }
    gh auth switch -u ykameyama 2>&1 | Out-Null
    if ($pushExit -ne 0) { return }
    Write-Host "  push しました（数分でサイトに反映されます）。"
    $script:PublishExit = 0
}

$steps = if ($SkipLinkCheck) { 3 } else { 5 }

Write-Host "[1/$steps] リモートの最新データを取り込みます..."
git fetch origin main
if ($LASTEXITCODE -ne 0) { Finish 1 }

$script:TempWorktree = Join-Path ([System.IO.Path]::GetTempPath()) (
    "poikatu-local-" + [Guid]::NewGuid().ToString("N")
)
git worktree add --detach $script:TempWorktree origin/main
if ($LASTEXITCODE -ne 0) { Finish 1 }
Set-Location $script:TempWorktree

Write-Host "[2/$steps] クロールします（対象: $Sites。数分かかります）..."
python run.py --sites $Sites
if ($LASTEXITCODE -ne 0) { Finish 1 }

# クロール結果は掲載終了チェック（約20分）の前に確定させる。CIでジョブを分けているのと
# 同じ理由で、重い後段に巻き込まれて取得済みデータを失わないようにする。
Write-Host "[3/$steps] クロール結果をコミットしてpushします..."
Publish-Data "data: local crawl ($Sites)"
if ($script:PublishExit -ne 0) { Finish 1 }

if ($SkipLinkCheck) {
    Write-Host "完了しました（掲載終了チェックは -SkipLinkCheck のため省略）。"
    Finish 0
}

# 掲載終了の検知もCIでは回れないので、このスクリプトが担う。掲載終了の確定には
# 連続2〜3回の dead 判定が必要（crawler/linkcheck.py の STREAK_HIGH/LOW）なので、
# 1回の実行で誤って消えることはない。サイト単位で並列に回るため、2サイトでも所要は
# 1サイト分（約20分）で済む。
Write-Host "[4/$steps] 掲載終了リンクをチェックします（対象: $Sites。約20分かかります）..."
python check_links.py --sites $Sites
if ($LASTEXITCODE -ne 0) { Finish 1 }

Write-Host "[5/$steps] 掲載終了の判定結果をコミットしてpushします..."
Publish-Data "data: local link check ($Sites)"
if ($script:PublishExit -ne 0) { Finish 1 }

Write-Host "完了しました。"
Finish 0
