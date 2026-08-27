<?php
/*
 クリック計測エンドポイント（人気案件ランキング用）。
 サイト共通JS（base.html.j2）が外部リンクのクリック時に navigator.sendBeacon で POST する。
 - 記録するのは「JST日付 × リンク先URLのsha1ハッシュ → クリック数」だけ。
   Cookieは使わず、IPアドレス・User-Agent等の個人を特定しうる情報は保存しない
 - GET ?export で全集計をJSONで返す（デイリークロールが取得して data/clicks.json へコミット）
 - 保存先は click-data/（同梱の .htaccess で直接アクセスを拒否）。FTPデプロイは自分が
   配布したファイルしか同期しないため、サーバ側で書き足されるデータファイルは消えない
*/

$dataFile = __DIR__ . '/click-data/clicks.json';
$KEEP_DAYS = 35;          // 保持日数（ランキングは直近7日集計。余裕を持って保持し古い日は捨てる）
$MAX_URL_LEN = 600;       // 受け付けるURL長の上限（異常入力対策）
$MAX_KEYS_PER_DAY = 5000; // 1日に記録する異なりURL数の上限（ディスク肥大対策）

header('Cache-Control: no-store');

$method = $_SERVER['REQUEST_METHOD'] ?? '';

if ($method === 'GET') {
    if (!isset($_GET['export'])) { http_response_code(404); exit; }
    header('Content-Type: application/json; charset=utf-8');
    echo is_file($dataFile) ? file_get_contents($dataFile) : '{"days":{}}';
    exit;
}

if ($method !== 'POST') { http_response_code(405); exit; }

// 不正な入力と、検索エンジン等のボットによるクリックは数えない（水増し防止。厳密でなくてよい）
$u = $_POST['u'] ?? '';
$ua = $_SERVER['HTTP_USER_AGENT'] ?? '';
if (!is_string($u) || $u === '' || strlen($u) > $MAX_URL_LEN
    || !preg_match('#^https?://#', $u)
    || preg_match('/bot|crawl|spider|slurp/i', $ua)) {
    http_response_code(204);
    exit;
}

$tz = new DateTimeZone('Asia/Tokyo');
$today = (new DateTime('now', $tz))->format('Y-m-d');
$hash = sha1($u);

// flockで排他し、読み→更新→書き戻しを不可分に行う（同時クリック時の取りこぼし・JSON破損防止）
$fp = @fopen($dataFile, 'c+');
if ($fp !== false) {
    if (flock($fp, LOCK_EX)) {
        $raw = stream_get_contents($fp);
        $data = json_decode($raw !== '' ? $raw : '{"days":{}}', true);
        if (!is_array($data) || !isset($data['days']) || !is_array($data['days'])) {
            $data = ['days' => []];
        }
        $day = $data['days'][$today] ?? [];
        if (isset($day[$hash]) || count($day) < $MAX_KEYS_PER_DAY) {
            $day[$hash] = ($day[$hash] ?? 0) + 1;
            $data['days'][$today] = $day;
            $cut = (new DateTime("-{$KEEP_DAYS} days", $tz))->format('Y-m-d');
            foreach (array_keys($data['days']) as $d) {
                if ($d < $cut) { unset($data['days'][$d]); }
            }
            ftruncate($fp, 0);
            rewind($fp);
            fwrite($fp, json_encode($data));
        }
        flock($fp, LOCK_UN);
    }
    fclose($fp);
}
http_response_code(204);
