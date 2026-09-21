import json
from datetime import date
from pathlib import Path

from horei.articles import auto_article, parse_markdown_article, pending
from horei.build import Builder, load_laws
from horei.model import Law
from test_model import raw


def make_data(tmp_path: Path) -> tuple[Path, Path]:
    data = tmp_path / "laws"
    data.mkdir()
    # 1. 労働基準法（既存法令、2027-04-01 に施行予定あり）
    (data / "322AC0000000049.json").write_text(json.dumps(raw()), encoding="utf-8")
    # 2. 新しく公布された法律（今週公布・来月施行）
    (data / "508AC0000000061.json").write_text(json.dumps(raw(
        law_info={"law_id": "508AC0000000061", "law_type": "Act", "law_num": "令和八年法律第六十一号",
                  "promulgation_date": "2026-09-22"},
        revision_info={"law_title": "防災庁設置法", "law_title_kana": "ぼうさいちょうせっちほう", "abbrev": None,
                       "category": "行政組織", "amendment_promulgate_date": "2026-09-22",
                       "amendment_enforcement_date": "2026-10-01", "amendment_law_title": None,
                       "amendment_law_num": None, "amendment_type": "1"},
        revisions=[{"law_revision_id": "508AC0000000061_20261001_000000000000000",
                    "amendment_promulgate_date": "2026-09-22", "amendment_enforcement_date": "2026-10-01",
                    "amendment_type": "1", "current_revision_status": "UnEnforced"}],
        toc=["第一章　総則"], article1=None)), encoding="utf-8")
    # 3. 厚生労働省令（今週施行・廃止済み）
    (data / "508M60000100012.json").write_text(json.dumps(raw(
        law_info={"law_id": "508M60000100012", "law_type": "MinisterialOrdinance",
                  "law_num": "令和八年厚生労働省令第十二号", "promulgation_date": "2026-02-10"},
        revision_info={"law_title": "テスト省令", "law_title_kana": "てすとしょうれい", "abbrev": None,
                       "category": "社会保険", "amendment_promulgate_date": "2026-02-10",
                       "amendment_enforcement_date": "2026-09-23", "repeal_status": "Repeal",
                       "repeal_date": "2026-09-23", "amendment_law_title": "テスト改正省令",
                       "amendment_law_num": "令和八年厚生労働省令第九十号"},
        revisions=[], toc=[], article1=None)), encoding="utf-8")
    articles = tmp_path / "articles"
    articles.mkdir()
    (articles / "322AC0000000049.md").write_text(
        "---\ntitle: 労働基準法はいつ何が変わるか\nsummary: 要約です\n---\n## 変わること\n本文です。\n", encoding="utf-8")
    return data, articles


def main_of(html: str) -> str:
    """サイドバーには全法令が並ぶので、「出てこないこと」を確かめるときは本文だけを見る。"""
    start = html.index('<main class="content">')
    return html[start:html.index("</main>", start)]


def build(tmp_path: Path):
    data, articles = make_data(tmp_path)
    out = tmp_path / "dist"
    today = date(2026, 9, 22)   # 火曜。週は 9/21〜9/27、来月は 10 月
    n = Builder(out, "https://example.com/site", today=today, data_dir=data, article_dir=articles).build()
    return out, n


def test_build_generates_every_page(tmp_path):
    out, n = build(tmp_path)
    assert n == 3
    for path in ("index.html", "law/322AC0000000049/index.html", "law/508AC0000000061/index.html",
                 "law/508M60000100012/index.html",
                 "enforced/this-week/index.html", "promulgated/this-week/index.html",
                 "upcoming/index.html", "upcoming/next-month/index.html",
                 "new/index.html", "amended/index.html", "repealed/index.html",
                 "type/index.html", "type/act/index.html", "type/ministerial-ordinance/index.html",
                 "category/index.html", "category/labor/index.html", "category/social-insurance/index.html",
                 "ministry/index.html", "ministry/mhlw/index.html",
                 "monthly/index.html", "monthly/2026-09/index.html", "monthly/2026-10/index.html",
                 "search/index.html", "search.json", "calendar.ics", "feed.xml", "sitemap.xml",
                 "robots.txt", "404.html", ".nojekyll", "about/index.html", "privacy/index.html",
                 "static/style.css", "static/favicon.svg"):
        assert (out / path).exists(), path


def test_law_page_uses_written_article(tmp_path):
    out, _ = build(tmp_path)
    page = (out / "law/322AC0000000049/index.html").read_text(encoding="utf-8")
    assert "労働基準法はいつ何が変わるか" in page and "本文です。" in page and "編集部の解説" in page
    assert 'href="https://example.com/site/static/style.css"' in page
    assert "2027年4月1日 施行予定" in page and "あと191日" in page
    assert "昭和二十二年法律第四十九号" in page
    assert '"@type":"Legislation"' in page and '"@type":"BreadcrumbList"' in page
    # 改正履歴の表に、施行済みの版と施行予定の版の両方が出る
    assert "労働者災害補償保険法等の一部を改正する法律" in page
    assert "施行予定" in page and "現行" in page


def test_law_page_falls_back_to_auto_article(tmp_path):
    out, _ = build(tmp_path)
    page = (out / "law/508AC0000000061/index.html").read_text(encoding="utf-8")
    assert "この法令は何を定めているか" in page and "編集部の解説" not in page
    assert "これから施行されること" in page and "2026年10月1日" in page


def test_weekly_and_monthly_grouping(tmp_path):
    out, _ = build(tmp_path)
    # 今週（9/21〜9/27）施行されたのは省令だけ（9/23 なので今日より後。改正履歴を見ないと拾えない）
    week = main_of((out / "enforced/this-week/index.html").read_text(encoding="utf-8"))
    assert "テスト省令" in week and "防災庁設置法" not in week
    # 今週公布されたのは防災庁設置法だけ
    pub = main_of((out / "promulgated/this-week/index.html").read_text(encoding="utf-8"))
    assert "防災庁設置法" in pub and "テスト省令" not in pub
    # 来月（10 月）施行予定
    nxt = main_of((out / "upcoming/next-month/index.html").read_text(encoding="utf-8"))
    assert "防災庁設置法" in nxt and "労働基準法" not in nxt
    # 新しく公布された法令
    new = main_of((out / "new/index.html").read_text(encoding="utf-8"))
    assert "防災庁設置法" in new and "労働基準法" not in new


def test_ministry_index_only_covers_ordinances(tmp_path):
    out, _ = build(tmp_path)
    mhlw = (out / "ministry/mhlw/index.html").read_text(encoding="utf-8")
    assert "テスト省令" in mhlw
    index = main_of((out / "ministry/index.html").read_text(encoding="utf-8"))
    assert "厚生労働省" in index
    # 法律は法令番号に府省名が入らないので府省別には出てこない
    assert "労働基準法" not in index


def test_search_json_and_ics_and_sitemap(tmp_path):
    out, _ = build(tmp_path)
    rows = json.loads((out / "search.json").read_text(encoding="utf-8"))
    assert {r["i"] for r in rows} == {"322AC0000000049", "508AC0000000061", "508M60000100012"}
    labor = next(r for r in rows if r["i"] == "322AC0000000049")
    assert labor["c"] == "労働" and labor["y"] == "act" and labor["u"] == "2027-04-01" and labor["s"] == "scheduled"
    ordinance = next(r for r in rows if r["i"] == "508M60000100012")
    assert ordinance["m"] == ["厚生労働省"] and ordinance["s"] == "repealed"

    ics = (out / "calendar.ics").read_text(encoding="utf-8")
    assert "BEGIN:VEVENT" in ics
    assert "DTSTART;VALUE=DATE:20270401" in ics      # 施行予定
    assert "DTSTART;VALUE=DATE:20260923" in ics      # 今週の施行
    assert "DTSTART;VALUE=DATE:20260717" not in ics  # 30 日より前の施行は入れない

    sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
    assert "https://example.com/site/law/322AC0000000049/" in sitemap
    assert "repealed/" not in sitemap                # noindex はサイトマップに入れない

    feed = (out / "feed.xml").read_text(encoding="utf-8")
    assert "防災庁設置法" in feed and "<rss" in feed


def test_pending_lists_unwritten_by_enforcement_date(tmp_path):
    data, articles = make_data(tmp_path)
    laws = load_laws(data)
    ids = [x.law_id for x in pending(laws, date(2026, 9, 22), article_dir=articles)]
    assert "322AC0000000049" not in ids                 # 解説を書いてある
    assert ids[0] == "508AC0000000061"                  # 施行予定がいちばん近い


def test_auto_article_quotes_article_one():
    x = Law.from_raw(raw())
    a = auto_article(x, date(2026, 9, 22))
    assert not a["written"]
    assert "人たるに値する生活" in a["body_html"]
    assert "e-Gov 法令検索" in a["body_html"]
    assert "2027年4月1日" in a["body_html"]


def test_parse_markdown_article():
    a = parse_markdown_article("---\ntitle: T\nsummary: S\n---\n## 見出し\n\n- 一つ\n- 二つ\n")
    assert a["title"] == "T" and a["summary"] == "S" and a["written"]
    assert "<h2>見出し</h2>" in a["body_html"] and "<li>一つ</li>" in a["body_html"]
