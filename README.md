# 法令ウォッチ

法律・政令・府省令・規則の**公布と施行**を、e-Gov 法令検索（デジタル庁）の公開 API から毎日取り込み、
静的サイトとして GitHub Pages に配信する。**運用費ゼロ**（API はキー不要、GitHub Actions と Pages は無料枠）。

- 公開先: https://bubbleman3333.github.io/horei_site/
- 狙い: 総務・人事・士業・経営者が週に一度見て「最近何が変わったか」「来月から何が変わるか」がわかること。
- 解説文は Claude（Claude Code のセッション）が書く。書いていない法令はデータから自動で組み立てた文章で埋まる。

## 仕組み

```
e-Gov 法令 API ──fetch──▶ data/laws/<法令ID>.json ──┐
                                                    ├─build──▶ dist/ ──▶ GitHub Pages
data/articles/<法令ID>.md（Claude が書く解説）──────┘
```

- `horei/egov.py` 取り込み。
- `horei/model.py` 生データ → `Law`。種別・分野・所管府省の整理、公布日・施行日・施行予定日の判定。
- `horei/articles.py` 解説の読み込み、解説が無いときの自動文。
- `horei/build.py` Jinja2 で `dist/` を書き出す。
- `.github/workflows/daily.yml` 毎朝 6 時（JST）に fetch → データをコミット → build → Pages に配信。

## 取り込みの範囲と方法

載せるのは**直近 1 年に公布または施行があった法令**と、**これから施行が決まっている法令**（約 2,700 件）。
1 年以上動きが無くなった法令は `data/laws/` から消える。

法令 API の `/laws` は `order` に `law_info` の項目しか効かず、改正日での絞り込みもできない。
そのため取り込みは次の 3 段階で行う。

1. **未来の時点**（今日 + 550 日）で `/laws?asof=…` を全件めくり、施行予定のある法令 ID を集める。
   未施行の版は現時点の一覧には出てこないので、この先読みが要る。
2. **現時点**で `/laws` を全件（約 9,600 件）めくり、公布日・改正公布日・改正施行日のどれかが
   直近 1 年に入るもの、または 1 で拾ったものを対象にする。
3. 対象ごとに `/law_revisions/{法令ID}`（改正履歴。未施行の版も入る）と
   `/law_data/{…}`（目次と第一条）を取る。本文は丸ごと取ると数 MB になるので `elm` で要素を絞る。

初回は約 2,700 件 × 3〜4 リクエストなので、8 スレッドで 30 分ほどかかる。
2 回目以降は、本文を取り直すのは e-Gov 側の `updated` が変わった法令だけ。

### つまずきやすいところ

- **まだ施行されていない法令**は、法令 ID だけで `/law_data` を引くと本文が空（`Sentence: [null]`）で返る。
  いちばん新しい版の法令履歴 ID を指定して取り直す（`egov.py` の `fetch_body` 呼び出しがそれ）。
- **第一条の場所は法令によって違う**（本則直下・章の下・編の下）。`ARTICLE_ELMS` を上から順に試す。
- **本文が大きい法令**は 200 で空のレスポンスが返ることがある。目次・条文なしとして扱う。
- **所管府省は API に無い**。法令番号（「令和八年厚生労働省令第十二号」）から読み取れる範囲だけ割り当てる。
  法律と政令は番号に府省名が入らないので、府省別の一覧には出てこない。
- `_meta.fetched_at` は「中身が変わった日」。毎日書き換えると全ファイルが git の差分になるので、
  内容が同じならファイルに触らない。

## 生成されるページ

| パス | 内容 |
| --- | --- |
| `/` | トップ。今週の施行・来月の施行・今週の公布・新しい法令・最近の改正 |
| `/law/<法令ID>/` | 法令 1 件のページ（要点表、目的条文、施行予定、改正履歴の表、e-Gov へのリンク） |
| `/enforced/this-week/` | 今週施行された法令 |
| `/promulgated/this-week/` | 今週公布された法令 |
| `/upcoming/next-month/` | 来月施行される法令 |
| `/upcoming/` | これから施行される法令（施行日順） |
| `/new/` `/amended/` `/repealed/` | 新しく公布された／最近改正が施行された／廃止・失効した法令 |
| `/type/<slug>/` | 種別ごと（法律・政令・府省令・規則） |
| `/category/<slug>/` | 分野ごと（e-Gov の事項別分類 50 種。労働・社会保険・国税など） |
| `/ministry/<slug>/` | 所管府省ごと（府省令・規則のみ） |
| `/monthly/<YYYY-MM>/` | 月ごとのまとめ |
| `/search/` | 分野・種別・府省・キーワードで絞り込む（`search.json` を JS で読む） |
| `/calendar.ics` | 施行日カレンダー（Google カレンダー等に登録できる） |
| `/feed.xml` `/sitemap.xml` `/robots.txt` `/404.html` | RSS・サイトマップ・robots・404 |
| `/about/` `/privacy/` | このサイトについて・プライバシーポリシー |

全ページに canonical・OGP・構造化データ（WebSite/SearchAction、Legislation、BreadcrumbList、CollectionPage）を出す。
ページ送りの 2 ページ目以降と `/repealed/` は noindex。

## コマンド（`.venv` を使う）

```powershell
.\.venv\Scripts\python -m horei fetch              # 取り込む（初回は 30 分ほど）
.\.venv\Scripts\python -m horei build --site-url http://127.0.0.1:8000   # ローカル確認用に生成
.\.venv\Scripts\python -m http.server 8000 -d dist  # ブラウザで http://127.0.0.1:8000/
.\.venv\Scripts\python -m horei pending             # 解説がまだ無い法令（施行予定が近い順）
.\.venv\Scripts\python -m horei digest <法令ID>     # 解説を書くための要約テキスト
.\.venv\Scripts\python -m pytest -q
```

## 解説を足す

`data/articles/README.md` の形式で `data/articles/<法令ID>.md` を置く。
法令 ID は `data/laws/` のファイル名（例: `322AC0000000049` = 労働基準法）。
push すれば Actions が生成して公開する。

## 広告を貼る

`config/affiliate.json` の各枠に広告タグの HTML をそのまま貼る。空なら何も出ない。
枠は `sidebar` / `article_top` / `article_bottom` / `list_bottom` / `head`。

## 設定

`config/site.json`。`site_url` は Pages の URL。`window_days`（何日前までさかのぼるか）、
`soon_days`（サイドバーの「まもなく施行」に出す日数）。
