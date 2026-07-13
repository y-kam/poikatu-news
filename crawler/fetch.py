"""対象サイトに負荷をかけないための礼儀正しいHTTPクライアント。

サイトごとにインスタンスを分け、リクエスト間隔の下限を強制する。
"""
import time

import requests

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# スマホ限定の案件（アプリDL・ゲーム等）を返すサイト向けのモバイルUA。
# PC UAではアプリ誘導ページや空の一覧しか返らないサイトで user_agent に指定する。
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)

# 実ブラウザのページ遷移に近いヘッダ。UAだけ・ヘッダ不足のリクエストを弾く
# 素朴なbot判定（WAF）を回避し、403 Forbidden を減らす。Accept-Encoding は
# requests が自動付与するため指定しない（brotli未導入なので br を要求しない）。
DEFAULT_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-CH-UA": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
}


# 断続的なブロック/過負荷を示すステータス。間隔を空けて再試行すると回復しやすい。
RETRY_STATUS = frozenset({403, 429, 503})


class PoliteFetcher:
    def __init__(self, interval: float = 10.0, timeout: float = 30.0,
                 user_agent: str = DEFAULT_UA, headers: dict | None = None,
                 max_retries: int = 0):
        self.interval = interval
        self.timeout = timeout
        self.max_retries = max_retries  # 403等での再試行回数（0で従来通り再試行しない）
        self._last_request_at = 0.0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.session.headers.update(DEFAULT_HEADERS)
        if headers:  # サイト固有の追加/上書きヘッダ（例: Referer）
            self.session.headers.update(headers)

    # 前回リクエストから interval 秒経つまで待つ（レート制御）
    def _wait(self) -> None:
        wait = self.interval - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def get(self, url: str, **kwargs) -> requests.Response:
        # max_retries>0 のサイトは、断続的な 403/429/503 を間隔を空けて再試行する。
        # 再試行前の待機は _wait() の interval がそのままバックオフとして働く。
        for attempt in range(self.max_retries + 1):
            self._wait()
            resp = self.session.get(url, timeout=self.timeout, **kwargs)
            if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                continue
            resp.raise_for_status()
            return resp

    # 公開ページのページ送りフォーム送信用（例: えんためねっとの2ページ目以降）。
    # レート制御・再試行は get と同一。運用ポリシー（GETのみ）の例外となるため、
    # 使用は手動バックフィルなど限定的な用途にとどめること。
    def post(self, url: str, **kwargs) -> requests.Response:
        for attempt in range(self.max_retries + 1):
            self._wait()
            resp = self.session.post(url, timeout=self.timeout, **kwargs)
            if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                continue
            resp.raise_for_status()
            return resp

    # リンク死活チェック用: ステータス例外を投げず、リダイレクトも追わない生のGET。
    # 404/410 やリダイレクト先URLを呼び出し側で判定できるようにする。
    def probe(self, url: str) -> requests.Response:
        self._wait()
        return self.session.get(url, timeout=self.timeout, allow_redirects=False)
