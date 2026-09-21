"""data/ から dist/ に静的サイトを書き出す。"""
from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

import markdown as md
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .articles import ARTICLE_DIR, article_for
from .model import (CATEGORIES, JST, LAW_TYPES, MINISTRIES, TYPE_ORDER, Law, fmt_date, month_key,
                    next_month_range, slug_of, week_range)

ROOT = Path(__file__).resolve().parent.parent
PER_PAGE = 50


def load_laws(data_dir: Path) -> list[Law]:
    out = []
    for path in sorted(data_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        law = Law.from_raw(raw)
        if law.law_id and law.title:
            out.append(law)
    return out


def load_config(root: Path = ROOT) -> tuple[dict, dict]:
    site = json.loads((root / "config" / "site.json").read_text(encoding="utf-8"))
    ads = json.loads((root / "config" / "affiliate.json").read_text(encoding="utf-8"))
    return site, ads


def in_range(d: date | None, start: date, end: date) -> bool:
    return bool(d and start <= d <= end)


def ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


class Builder:
    def __init__(self, out_dir: Path, site_url: str, today: date | None = None, data_dir: Path | None = None,
                 article_dir: Path = ARTICLE_DIR, root: Path = ROOT):
        self.out = out_dir
        self.site_url = site_url.rstrip("/")
        self.today = today or datetime.now(JST).date()
        self.data_dir = data_dir or root / "data" / "laws"
        self.article_dir = article_dir
        self.root = root
        self.site, self.ads = load_config(root)
        self.env = Environment(loader=FileSystemLoader(root / "templates"), autoescape=select_autoescape(["html", "xml"]))
        self.env.filters["date"] = fmt_date
        self.env.globals.update(url=self.url, site=self.site, ads=self.ads, today=self.today,
                                LAW_TYPES=LAW_TYPES, CATEGORIES=CATEGORIES,
                                ministry_slug=lambda m: slug_of(MINISTRIES, m))
        self.sitemap: list[tuple[str, date | None]] = []

    # ---- 部品 ----
    def url(self, path: str = "") -> str:
        return f"{self.site_url}/{path.lstrip('/')}"

    def write(self, path: str, text: str) -> None:
        target = self.out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def render(self, template: str, path: str, *, noindex: bool = False, lastmod: date | None = None, **ctx) -> None:
        canonical = self.url(path.replace("index.html", ""))
        html = self.env.get_template(template).render(canonical=canonical, noindex=noindex, **ctx)
        self.write(path, html)
        if not noindex:
            self.sitemap.append((canonical, lastmod))

    def paginate(self, base: str, items: list[Law], *, title: str, description: str, noindex: bool = False,
                 template: str = "list.html", **ctx) -> None:
        pages = max(1, (len(items) + PER_PAGE - 1) // PER_PAGE)
        for i in range(pages):
            chunk = items[i * PER_PAGE:(i + 1) * PER_PAGE]
            path = f"{base}index.html" if i == 0 else f"{base}page/{i + 1}/index.html"
            self.render(template, path, noindex=noindex or i > 0,
                        title=title if i == 0 else f"{title}（{i + 1}ページ目）", description=description,
                        items=chunk, page=i + 1, pages=pages, base=base, count=len(items), **ctx)

    # ---- 本体 ----
    def build(self) -> int:
        if self.out.exists():
            shutil.rmtree(self.out)
        self.out.mkdir(parents=True)
        shutil.copytree(self.root / "static", self.out / "static")
        laws = load_laws(self.data_dir)
        today = self.today
        since = today - timedelta(days=self.site["window_days"])
        wk_start, wk_end = week_range(today)
        nm_start, nm_end = next_month_range(today)
        soon = today + timedelta(days=self.site["soon_days"])

        def by_enforced(lst: list[Law]) -> list[Law]:
            return sorted(lst, key=lambda x: (x.enforced or date.min), reverse=True)

        # 新しく作られた法令（直近 1 年に公布されたもの）
        new_laws = sorted([x for x in laws if x.is_new_law(since)], key=lambda x: x.promulgated or date.min, reverse=True)
        new_ids = {x.law_id for x in new_laws}
        # 既存の法令の改正（直近 1 年に施行されたもの）
        amended = by_enforced([x for x in laws if x.enforced and x.enforced >= since and x.law_id not in new_ids])
        # これから施行される
        upcoming = sorted([x for x in laws if x.next_enforcement(today)], key=lambda x: x.next_enforcement(today))
        upcoming_soon = [x for x in upcoming if x.next_enforcement(today) <= soon]
        next_month = [x for x in upcoming if in_range(x.next_enforcement(today), nm_start, nm_end)]
        # 今週（今日より後に施行・公布される分も入れたいので、改正履歴の日付まで見る）
        enforced_week = sorted([x for x in laws if x.enforced_between(wk_start, wk_end)],
                               key=lambda x: x.enforced_between(wk_start, wk_end), reverse=True)
        promulgated_week = sorted([x for x in laws if x.promulgated_between(wk_start, wk_end)],
                                  key=lambda x: x.promulgated_between(wk_start, wk_end), reverse=True)
        repealed = by_enforced([x for x in laws if x.is_repealed])

        # 分類ごと
        by_type: dict[str, list[Law]] = defaultdict(list)
        by_category: dict[str, list[Law]] = defaultdict(list)
        by_ministry: dict[str, list[Law]] = defaultdict(list)
        by_month: dict[str, list[Law]] = defaultdict(list)
        for x in laws:
            by_type[x.law_type].append(x)
            if x.category:
                by_category[x.category].append(x)
            for m in x.ministries:
                by_ministry[m].append(x)
            # 月ごとのまとめは、掲載の範囲（直近 1 年〜これから）に入る公布日・施行日だけを数える
            months = {month_key(d) for d in (x.promulgation_dates() + x.enforcement_dates()) if d >= since}
            for m in months:
                by_month[m].append(x)

        type_index = [(LAW_TYPES[t][0], LAW_TYPES[t][1], len(by_type[t])) for t in TYPE_ORDER if by_type.get(t)]
        category_index = sorted([(c, slug_of(CATEGORIES, c), len(v)) for c, v in by_category.items()], key=lambda t: -t[2])
        ministry_index = sorted([(m, slug_of(MINISTRIES, m), len(v)) for m, v in by_ministry.items()], key=lambda t: -t[2])
        months = sorted(by_month.keys(), reverse=True)
        month_index = [(m, len(by_month[m])) for m in months]
        latest_fetch = max((x.fetched_at for x in laws if x.fetched_at), default=today)

        self.env.globals.update(
            type_index=type_index, category_index=category_index, ministry_index=ministry_index,
            month_index=month_index[:12], sidebar_upcoming=upcoming_soon[:8], sidebar_enforced=amended[:6],
            stats=dict(total=len(laws), new=len(new_laws), amended=len(amended), upcoming=len(upcoming),
                       next_month=len(next_month), week=len(enforced_week), updated=latest_fetch,
                       categories=len(by_category), ministries=len(by_ministry)))

        # 法令ページ
        for x in laws:
            art = article_for(x, today, self.article_dir)
            pool = by_category.get(x.category, []) if x.category else by_type.get(x.law_type, [])
            related = [r for r in by_enforced(pool) if r is not x][:6]
            self.render("law.html", f"{x.path}index.html", law=x, art=art, related=related,
                        future=x.future_revisions(today), lastmod=x.fetched_at)

        # 入口
        self.render("index.html", "index.html",
                    enforced_week=enforced_week[:8], promulgated_week=promulgated_week[:8],
                    next_month=next_month[:10], new_laws=new_laws[:10], amended=amended[:10], upcoming=upcoming[:10])

        w = f"{wk_start.month}月{wk_start.day}日〜{wk_end.month}月{wk_end.day}日"
        self.paginate("enforced/this-week/", enforced_week, title="今週施行された法令",
                      description=f"{wk_start.year}年{w}に施行された法律・政令・府省令の一覧です。毎朝自動で更新しています。")
        self.paginate("promulgated/this-week/", promulgated_week, title="今週公布された法令",
                      description=f"{wk_start.year}年{w}に公布された法律・政令・府省令の一覧です。新しく作られたものと、既存の法令を改正するものを含みます。")
        self.paginate("upcoming/next-month/", next_month, title=f"{nm_start.year}年{nm_start.month}月に施行される法令",
                      description=f"{nm_start.year}年{nm_start.month}月に施行が予定されている法令の一覧です。就業規則や社内規程の見直しが必要になるものが含まれます。")
        self.paginate("upcoming/", upcoming, title="これから施行される法令",
                      description="施行日がこれから来る法令を、施行日の近い順に並べています。施行日は政令で後から決まることがあります。")
        self.paginate("new/", new_laws, title="新しく公布された法令",
                      description=f"直近{self.site['window_days']}日に公布された法律・政令・府省令・規則です。新規制定のほか、独立した法令として登録された改正法令を含みます。")
        self.paginate("amended/", amended, title="最近改正された法令",
                      description=f"直近{self.site['window_days']}日に改正が施行された法令を、施行日の新しい順に並べています。")
        self.paginate("repealed/", repealed, title="廃止・失効した法令",
                      description="廃止・失効・実効性喪失となった法令です。", noindex=True)

        for name, slug, _ in type_index:
            key = next(t for t in TYPE_ORDER if LAW_TYPES[t][1] == slug)
            self.paginate(f"type/{slug}/", by_enforced(by_type[key]), title=f"最近動きのあった{name}の一覧",
                          description=f"直近{self.site['window_days']}日に公布・施行・改正があった{name}の一覧です。",
                          kind="type", key=name)
        for name, slug, _ in category_index:
            self.paginate(f"category/{slug}/", by_enforced(by_category[name]), title=f"「{name}」分野の法令改正",
                          description=f"e-Gov 法令検索で「{name}」に分類されている法令のうち、最近動きがあったものの一覧です。",
                          kind="category", key=name)
        for name, slug, _ in ministry_index:
            self.paginate(f"ministry/{slug}/", by_enforced(by_ministry[name]), title=f"{name}の府省令・規則",
                          description=f"法令番号から{name}が定めたと読み取れる府省令・規則のうち、最近動きがあったものの一覧です。",
                          kind="ministry", key=name)
        for m in months:
            y, mm = m.split("-")
            self.paginate(f"monthly/{m}/", by_enforced(by_month[m]), title=f"{y}年{int(mm)}月の法令まとめ",
                          description=f"{y}年{int(mm)}月に公布・施行された、または施行が予定されている法令 {len(by_month[m])} 件のまとめです。",
                          kind="month", key=m)

        self.render("groups.html", "type/index.html", title="法令の種別から探す",
                    description="法律・政令・府省令・規則の別に、最近動きのあった法令を並べています。",
                    rows=type_index, base="type")
        self.render("groups.html", "category/index.html", title="分野から探す",
                    description="e-Gov 法令検索の事項別分類（労働・社会保険・国税など）ごとに、最近動きのあった法令を並べています。",
                    rows=category_index, base="category")
        self.render("groups.html", "ministry/index.html", title="所管府省から探す",
                    description="法令番号から所管府省が読み取れる府省令・規則を、府省ごとに並べています。法律と政令は番号に府省名が入らないため、ここには出てきません。",
                    rows=ministry_index, base="ministry")
        self.render("months.html", "monthly/index.html", title="月ごとの法令まとめ",
                    description="公布・施行があった月ごとに法令をまとめています。", rows=month_index)
        self.render("search.html", "search/index.html", title="法令を検索する",
                    description="法令名・分野・種別・施行日から、最近動きのあった法令を絞り込めます。")

        # 固定ページ
        for name in ("about", "privacy"):
            text = (self.root / "content" / f"{name}.md").read_text(encoding="utf-8")
            title = text.splitlines()[0].lstrip("# ").strip()
            body = md.markdown("\n".join(text.splitlines()[1:]))
            self.render("page.html", f"{name}/index.html", title=title, description=title, body=body)
        self.write("404.html", self.env.get_template("404.html").render(canonical=self.url("404.html"), noindex=True))

        # 機械向け
        self.write("search.json", self.search_json(laws))
        self.write("calendar.ics", self.ics(laws, "法令の施行日"))
        self.write_sitemap()
        self.write_feed(sorted(laws, key=lambda x: (x.last_promulgated or date.min), reverse=True)[:40])
        self.write("robots.txt", f"User-agent: *\nAllow: /\nSitemap: {self.url('sitemap.xml')}\n")
        self.write(".nojekyll", "")
        return len(laws)

    def search_json(self, laws: list[Law]) -> str:
        rows = []
        for x in laws:
            nxt = x.next_enforcement(self.today)
            rows.append({"i": x.law_id, "t": x.title, "k": x.title_kana, "b": x.abbrev, "n": x.law_num,
                         "y": x.type_slug, "yl": x.type_label, "c": x.category, "m": x.ministries,
                         "p": x.promulgated.isoformat() if x.promulgated else None,
                         "e": x.enforced.isoformat() if x.enforced else None,
                         "u": nxt.isoformat() if nxt else None,
                         "s": x.status(self.today)[0]})
        return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))

    def ics(self, laws: list[Law], name: str) -> str:
        """施行日のカレンダー。これから施行される分と、直近 30 日に施行された分を入れる。"""
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//{self.site['name']}//JA", "CALSCALE:GREGORIAN",
                 "METHOD:PUBLISH", f"X-WR-CALNAME:{ics_escape(name)}", "X-WR-TIMEZONE:Asia/Tokyo"]
        stamp = datetime.now(JST).strftime("%Y%m%dT%H%M%SZ")
        floor = self.today - timedelta(days=30)
        seen: set[tuple[str, str]] = set()
        for x in laws:
            days = [(x.enforced, x.amend_law_title)] + [(r.enforcement, r.law_title) for r in x.future_revisions(self.today)]
            for d, by in days:
                if not d or d < floor:
                    continue
                key = (x.law_id, d.isoformat())
                if key in seen:
                    continue
                seen.add(key)
                nxt = (d + timedelta(days=1)).strftime("%Y%m%d")
                desc = (f"{x.law_num}\n" + (f"改正: {by}\n" if by else "") + self.url(x.path))
                lines += ["BEGIN:VEVENT", f"UID:{x.law_id}-{d.isoformat()}@horei", f"DTSTAMP:{stamp}",
                          f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{nxt}",
                          f"SUMMARY:{ics_escape('【施行】' + x.title)}", f"DESCRIPTION:{ics_escape(desc)}",
                          f"URL:{self.url(x.path)}", "END:VEVENT"]
        lines.append("END:VCALENDAR")
        return "\r\n".join(lines) + "\r\n"

    def write_sitemap(self) -> None:
        rows = []
        for loc, lastmod in self.sitemap:
            lm = f"<lastmod>{lastmod.isoformat()}</lastmod>" if lastmod else ""
            rows.append(f"<url><loc>{escape(loc)}</loc>{lm}</url>")
        self.write("sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                   + "\n".join(rows) + "\n</urlset>\n")

    def write_feed(self, laws: list[Law]) -> None:
        entries = []
        for x in laws:
            art = article_for(x, self.today, self.article_dir)
            d = x.last_promulgated or self.today
            pub = datetime(d.year, d.month, d.day, 9, 0, tzinfo=JST).strftime("%a, %d %b %Y %H:%M:%S %z")
            link = escape(self.url(x.path))
            entries.append(f"<item><title>{escape(x.title)}</title><link>{link}</link><guid>{link}</guid>"
                           f"<pubDate>{pub}</pubDate><description>{escape(art['summary'])}</description></item>")
        self.write("feed.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
                   f"<title>{escape(self.site['name'])}</title><link>{escape(self.url())}</link>"
                   f"<description>{escape(self.site['description'])}</description>\n"
                   + "\n".join(entries) + "\n</channel></rss>\n")
