from datetime import date

from horei.egov import extract_article, extract_toc, is_target
from horei.model import (Law, fmt_date, ministries_of, month_key, next_month_range, parse_date,
                         slug_of, CATEGORIES, week_range)


def raw(**over):
    """テスト用の生データ。e-Gov 法令 API が返す形に合わせてある。"""
    base = {
        "law_info": {
            "law_type": "Act", "law_id": "322AC0000000049", "law_num": "昭和二十二年法律第四十九号",
            "law_num_era": "Showa", "law_num_year": 22, "law_num_type": "Act", "law_num_num": "049",
            "promulgation_date": "1947-04-07",
        },
        "revision_info": {
            "law_revision_id": "322AC0000000049_20260717_508AC0000000060", "law_type": "Act",
            "law_title": "労働基準法", "law_title_kana": "ろうどうきじゅんほう", "abbrev": "労基法",
            "category": "労働", "updated": "2026-07-17T10:45:13+09:00",
            "amendment_promulgate_date": "2026-07-17", "amendment_enforcement_date": "2026-07-17",
            "amendment_enforcement_comment": None, "amendment_scheduled_enforcement_date": None,
            "amendment_law_id": "508AC0000000060", "amendment_law_title": "労働者災害補償保険法等の一部を改正する法律",
            "amendment_law_num": "令和八年法律第六十号", "amendment_type": "3", "repeal_status": "None",
            "repeal_date": None, "mission": "New", "current_revision_status": "CurrentEnforced",
        },
        "revisions": [
            {"law_revision_id": "322AC0000000049_20270401_508AC0000000060",
             "amendment_promulgate_date": "2026-07-17", "amendment_enforcement_date": "2027-04-01",
             "amendment_scheduled_enforcement_date": None,
             "amendment_law_title": "労働者災害補償保険法等の一部を改正する法律",
             "amendment_law_num": "令和八年法律第六十号", "amendment_type": "3",
             "current_revision_status": "UnEnforced"},
            {"law_revision_id": "322AC0000000049_20260717_508AC0000000060",
             "amendment_promulgate_date": "2026-07-17", "amendment_enforcement_date": "2026-07-17",
             "amendment_law_title": "労働者災害補償保険法等の一部を改正する法律",
             "amendment_law_num": "令和八年法律第六十号", "amendment_type": "3",
             "current_revision_status": "CurrentEnforced"},
        ],
        "toc": ["第一章　総則", "第二章　労働契約"],
        "article1": {"caption": "（労働条件の原則）", "title": "第一条",
                     "paragraphs": ["労働条件は、労働者が人たるに値する生活を営むための必要を充たすべきものでなければならない。"]},
        "_meta": {"first_seen": "2026-07-17", "fetched_at": "2026-09-20", "updated": "2026-07-17T10:45:13+09:00"},
    }
    for key, value in over.items():
        if key in ("law_info", "revision_info"):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return base


def test_parse_date():
    assert parse_date("2026-04-01") == date(2026, 4, 1)
    assert parse_date("2026-04-01T09:00:00+09:00") == date(2026, 4, 1)
    assert parse_date(None) is None
    assert parse_date("") is None
    assert parse_date("わからない") is None


def test_ministries_from_law_num():
    assert ministries_of("令和八年内閣府令第七十二号") == ["内閣府"]
    assert ministries_of("令和七年厚生労働省令第十二号") == ["厚生労働省"]
    assert ministries_of("令和五年経済産業省・環境省令第一号") == ["経済産業省", "環境省"]
    assert ministries_of("令和八年国家公安委員会規則第三号") == ["国家公安委員会"]
    assert ministries_of("令和八年デジタル庁令第一号") == ["デジタル庁"]
    # 法律・政令には府省名が入らない
    assert ministries_of("昭和二十二年法律第四十九号") == []
    assert ministries_of("令和八年政令第二百七十二号") == []
    assert ministries_of(None) == []


def test_slug_falls_back_to_hash():
    assert slug_of(CATEGORIES, "労働") == "labor"
    assert slug_of(CATEGORIES, "知らない分類").startswith("x")


def test_law_basic_fields():
    x = Law.from_raw(raw())
    assert x.title == "労働基準法" and x.abbrev == "労基法"
    assert x.type_label == "法律" and x.type_slug == "act"
    assert x.category_slug == "labor"
    assert x.path == "law/322AC0000000049/"
    assert x.egov_url.endswith("/law/322AC0000000049")
    assert x.promulgated == date(1947, 4, 7)
    assert x.enforced == date(2026, 7, 17)
    assert x.ministries == []
    assert "人たるに値する生活" in x.purpose_text
    assert x.summary_line.startswith("労働条件は")


def test_future_revisions_and_status():
    x = Law.from_raw(raw())
    today = date(2026, 9, 21)
    future = x.future_revisions(today)
    assert len(future) == 1 and future[0].enforcement == date(2027, 4, 1)
    assert x.next_enforcement(today) == date(2027, 4, 1)
    assert x.days_until(today) == 192
    assert x.status(today) == ("scheduled", "2027年4月1日 施行予定")
    # 施行予定日を過ぎれば「施行中」になる
    after = date(2027, 5, 1)
    assert x.future_revisions(after) == []
    assert x.status(after) == ("enforced", "施行中")


def test_status_recently_enforced_and_repealed():
    x = Law.from_raw(raw(revisions=[]))
    assert x.status(date(2026, 7, 20)) == ("enforced", "施行されたばかり")
    assert x.status(date(2026, 12, 1)) == ("enforced", "施行中")
    dead = Law.from_raw(raw(revision_info={"repeal_status": "Repeal", "repeal_date": "2026-03-31"}, revisions=[]))
    assert dead.is_repealed and dead.status(date(2026, 9, 21)) == ("repealed", "廃止")


def test_is_new_law_and_last_promulgated():
    x = Law.from_raw(raw())
    assert not x.is_new_law(date(2025, 9, 21))       # 1947 年公布なので「新しい法令」ではない
    assert x.last_promulgated == date(2026, 7, 17)   # 改正法令の公布日のほうが新しい
    fresh = Law.from_raw(raw(law_info={"promulgation_date": "2026-07-31"}))
    assert fresh.is_new_law(date(2025, 9, 21))


def test_enforcement_and_promulgation_dates_include_revisions():
    x = Law.from_raw(raw())
    # 現行の版（2026-07-17）と、施行予定の版（2027-04-01）の両方が入る
    assert x.enforcement_dates() == [date(2026, 7, 17), date(2027, 4, 1)]
    assert x.promulgation_dates() == [date(1947, 4, 7), date(2026, 7, 17)]
    # 今日より後でも、その週のうちなら「今週の施行」として拾える
    assert x.enforced_between(date(2027, 3, 29), date(2027, 4, 4)) == date(2027, 4, 1)
    assert x.enforced_between(date(2026, 1, 1), date(2026, 1, 31)) is None
    assert x.promulgated_between(date(2026, 7, 13), date(2026, 7, 19)) == date(2026, 7, 17)


def test_is_new_uses_latest_move():
    x = Law.from_raw(raw())
    assert x.is_new(date(2026, 7, 20), 14)
    assert not x.is_new(date(2026, 9, 21), 14)


def test_week_and_month_helpers():
    assert week_range(date(2026, 9, 23)) == (date(2026, 9, 21), date(2026, 9, 27))
    assert week_range(date(2026, 9, 21)) == (date(2026, 9, 21), date(2026, 9, 27))
    assert next_month_range(date(2026, 9, 21)) == (date(2026, 10, 1), date(2026, 10, 31))
    assert next_month_range(date(2026, 12, 5)) == (date(2027, 1, 1), date(2027, 1, 31))
    assert month_key(date(2026, 4, 1)) == "2026-04"
    assert fmt_date(date(2026, 4, 1)) == "2026年4月1日"
    assert fmt_date(None) == "記載なし"


def test_is_target_window():
    since = date(2025, 9, 21)
    assert is_target(raw(), since)                                    # 2026-07-17 施行
    old = raw(law_info={"promulgation_date": "1947-04-07"},
              revision_info={"amendment_promulgate_date": "2010-01-01", "amendment_enforcement_date": "2010-04-01"})
    assert not is_target(old, since)


def test_extract_toc_prefers_chapters():
    full = {"TOC": {"TOCLabel": "目次", "TOCChapter": [
        {"ChapterTitle": ["第一章　総則"], "ArticleRange": "（第一条）",
         "TOCSection": [{"SectionTitle": ["第一節　通則"]}]},
        {"ChapterTitle": ["第二章　労働契約"]}]}}
    assert extract_toc(full) == ["第一章　総則", "第二章　労働契約"]
    # 章が無ければ条の見出しで代える
    only_articles = {"TOC": {"TOCArticle": [{"ArticleTitle": ["第一条"]}, {"ArticleTitle": ["第二条"]}]}}
    assert extract_toc(only_articles) == ["第一条", "第二条"]
    assert extract_toc(None) == [] and extract_toc({}) == []


def test_extract_article():
    full = {"Article": {"ArticleCaption": "（目的）", "ArticleTitle": "第一条", "Paragraph": [
        {"Num": "1", "ParagraphSentence": {"Sentence": ["この法律は、◯◯を目的とする。"]}},
        {"Num": "2", "ParagraphSentence": {"Sentence": ["前項の規定は、◯◯について準用する。"]}}]}}
    a = extract_article(full)
    assert a["caption"] == "（目的）" and a["title"] == "第一条"
    assert a["paragraphs"] == ["この法律は、◯◯を目的とする。", "前項の規定は、◯◯について準用する。"]
    # まだ施行されていない法令は中身が空で返ってくる。その場合は None
    assert extract_article({"Paragraph": {"Num": "1", "ParagraphSentence": {"Sentence": [None]}}}) is None
    assert extract_article(None) is None
