"""e-Gov 法令 API の生データ 1 件を、テンプレートから扱いやすい形（Law）に直す。

API が返すのは「法令情報（law_info）」と「その時点の版の情報（revision_info）」。
このサイトで使うのは、法令名・種別・分野・公布日・施行日・改正履歴・本文の冒頭だけ。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

# 法令種別（law_type）→ 表示名と slug
LAW_TYPES: dict[str, tuple[str, str]] = {
    "Constitution": ("憲法", "constitution"),
    "Act": ("法律", "act"),
    "CabinetOrder": ("政令", "cabinet-order"),
    "ImperialOrder": ("勅令", "imperial-order"),
    "MinisterialOrdinance": ("府省令", "ministerial-ordinance"),
    "Rule": ("規則", "rule"),
    "Misc": ("その他", "misc"),
}
# 一覧に出す順番（新しく動きがあるものから）
TYPE_ORDER = ["Act", "CabinetOrder", "MinisterialOrdinance", "Rule", "Constitution", "ImperialOrder", "Misc"]

# 事項別分類（revision_info.category は名前で返ってくる）→ slug
CATEGORIES: dict[str, str] = {
    "憲法": "constitution", "刑事": "criminal", "財務通則": "finance-general", "水産業": "fishery",
    "観光": "tourism", "国会": "diet", "警察": "police", "国有財産": "national-property",
    "鉱業": "mining", "郵務": "postal", "行政組織": "admin-organization", "消防": "fire-service",
    "国税": "national-tax", "工業": "industry", "電気通信": "telecom", "国家公務員": "national-public-service",
    "国土開発": "land-development", "事業": "business", "商業": "commerce", "労働": "labor",
    "行政手続": "admin-procedure", "土地": "land", "国債": "national-bond", "金融・保険": "finance-insurance",
    "環境保全": "environment", "統計": "statistics", "都市計画": "city-planning", "教育": "education",
    "外国為替・貿易": "foreign-exchange-trade", "厚生": "health", "地方自治": "local-government",
    "道路": "road", "文化": "culture", "陸運": "land-transport", "社会福祉": "social-welfare",
    "地方財政": "local-finance", "河川": "river", "産業通則": "industry-general", "海運": "marine-transport",
    "社会保険": "social-insurance", "司法": "justice", "災害対策": "disaster", "農業": "agriculture",
    "航空": "aviation", "防衛": "defense", "民事": "civil", "建築・住宅": "building-housing",
    "林業": "forestry", "貨物運送": "freight", "外事": "foreign-affairs",
}

# 所管府省（法令番号から読み取れるもの）→ slug。ここに無いものはハッシュの slug に落ちる
MINISTRIES: dict[str, str] = {
    "内閣府": "cabinet-office", "デジタル庁": "digital", "復興庁": "reconstruction",
    "総務省": "soumu", "法務省": "moj", "外務省": "mofa", "財務省": "mof",
    "文部科学省": "mext", "厚生労働省": "mhlw", "農林水産省": "maff", "経済産業省": "meti",
    "国土交通省": "mlit", "環境省": "env", "防衛省": "mod",
    "国家公安委員会": "npsc", "公正取引委員会": "jftc", "個人情報保護委員会": "ppc",
    "原子力規制委員会": "nra", "人事院": "jinjiin", "会計検査院": "jbaudit",
    "金融庁": "fsa", "公害等調整委員会": "soumu-eac", "中央労働委員会": "churoi",
    "カジノ管理委員会": "casino",
    # 以前の府省（古い法令の番号にそのまま残っている）
    "総理府": "sorifu", "大蔵省": "okurasho", "厚生省": "koseisho", "労働省": "rodosho",
    "運輸省": "unyusho", "建設省": "kensetsusho", "自治省": "jichisho", "通商産業省": "tsusansho",
    "農林省": "norinsho", "文部省": "monbusho", "郵政省": "yuseisho", "内務省": "naimusho",
    "司法省": "shihosho", "逓信省": "teishinsho", "法務府": "homufu",
}

# 「令和八年厚生労働省令第十二号」→ 真ん中の「厚生労働省令」を取り出す
LAW_NUM_RE = re.compile(r"^(?:明治|大正|昭和|平成|令和)[〇一二三四五六七八九十]+年(.+?)第[〇一二三四五六七八九十百千]+号$")

# 改正の種類（amendment_type）
AMENDMENT_TYPES = {"1": "新規制定", "3": "被改正", "8": "廃止"}
# 廃止等の状態（repeal_status）
REPEAL_LABELS = {"Repeal": "廃止", "Expire": "失効", "Suspend": "停止", "LossOfEffectiveness": "実効性喪失"}


def slug_of(table: dict[str, str], name: str) -> str:
    if name in table:
        return table[name]
    return "x" + hashlib.md5(name.encode("utf-8")).hexdigest()[:8]


def parse_date(value: str | None) -> date | None:
    """'2026-04-01' → date。読めなければ None。"""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def ministries_of(law_num: str | None) -> list[str]:
    """法令番号から所管府省を読み取る。

    「令和八年内閣府令第七十二号」→ ['内閣府']、「令和七年経済産業省・環境省令第一号」→ ['経済産業省', '環境省']、
    「令和八年国家公安委員会規則第三号」→ ['国家公安委員会']。法律・政令には府省が書かれていないので空。
    """
    m = LAW_NUM_RE.match((law_num or "").strip())
    if not m:
        return []
    body = m.group(1)
    if body.endswith("規則"):
        name = body[:-2]
        return [name] if name and name not in ("", "閣") else []
    if body.endswith("令"):
        body = body[:-1]
        if body in ("法律", "政", "勅", "閣", "太政官布告", "太政官達", ""):
            return []
        out = [p for p in body.split("・") if p]
        # 「内閣府・厚生労働省」のように、最後だけ「省」が落ちていることは無い（令を外しただけなので）
        return out
    return []


def era_year(law_num: str | None) -> str:
    """法令番号の元号部分（'令和八年'）。並べ替えや表示には使わず、参考情報として持つだけ。"""
    m = re.match(r"^((?:明治|大正|昭和|平成|令和)[〇一二三四五六七八九十]+年)", (law_num or "").strip())
    return m.group(1) if m else ""


@dataclass
class Revision:
    """改正履歴の 1 行。"""
    revision_id: str
    promulgate: date | None       # 改正法令の公布日
    enforcement: date | None      # この版の施行日
    scheduled: date | None        # 施行予定日（未施行のときに入る）
    comment: str                  # 施行に関する注記
    law_title: str                # 改正法令の名前
    law_num: str                  # 改正法令の番号
    law_id: str                   # 改正法令の法令 ID
    status: str                   # CurrentEnforced / UnEnforced / PreviousEnforced / Repeal
    amendment_type: str

    @classmethod
    def from_raw(cls, r: dict) -> "Revision":
        return cls(
            revision_id=r.get("law_revision_id") or "",
            promulgate=parse_date(r.get("amendment_promulgate_date")),
            enforcement=parse_date(r.get("amendment_enforcement_date")),
            scheduled=parse_date(r.get("amendment_scheduled_enforcement_date")),
            comment=(r.get("amendment_enforcement_comment") or "").strip(),
            law_title=(r.get("amendment_law_title") or "").strip(),
            law_num=(r.get("amendment_law_num") or "").strip(),
            law_id=(r.get("amendment_law_id") or "").strip(),
            status=r.get("current_revision_status") or "",
            amendment_type=str(r.get("amendment_type") or ""),
        )

    def to_raw(self) -> dict:
        def s(d: date | None) -> str | None:
            return d.isoformat() if d else None
        return {"law_revision_id": self.revision_id, "amendment_promulgate_date": s(self.promulgate),
                "amendment_enforcement_date": s(self.enforcement), "amendment_scheduled_enforcement_date": s(self.scheduled),
                "amendment_enforcement_comment": self.comment or None, "amendment_law_title": self.law_title or None,
                "amendment_law_num": self.law_num or None, "amendment_law_id": self.law_id or None,
                "current_revision_status": self.status, "amendment_type": self.amendment_type}

    @property
    def kind(self) -> str:
        return AMENDMENT_TYPES.get(self.amendment_type, "改正")

    def is_future(self, today: date) -> bool:
        return bool(self.enforcement and self.enforcement > today)


@dataclass
class Law:
    law_id: str
    law_type: str
    law_num: str
    title: str
    title_kana: str
    abbrev: str
    category: str
    promulgated: date | None          # 公布日（その法令そのものが公布された日）
    amended: date | None              # いま有効な版のもとになった改正法令の公布日
    enforced: date | None             # いま有効な版の施行日
    amend_law_title: str
    amend_law_num: str
    repeal_status: str
    repeal_date: date | None
    revisions: list[Revision] = field(default_factory=list)   # 新しい順
    toc: list[str] = field(default_factory=list)              # 目次（章のタイトル）
    article1: dict | None = None                              # 第一条（{"caption","title","paragraphs"}）
    first_seen: date | None = None
    fetched_at: date | None = None

    @classmethod
    def from_raw(cls, raw: dict) -> "Law":
        li = raw.get("law_info") or {}
        ri = raw.get("revision_info") or {}
        meta = raw.get("_meta") or {}
        return cls(
            law_id=li.get("law_id") or "",
            law_type=li.get("law_type") or ri.get("law_type") or "Misc",
            law_num=(li.get("law_num") or "").strip(),
            title=(ri.get("law_title") or "").strip(),
            title_kana=(ri.get("law_title_kana") or "").strip(),
            abbrev=(ri.get("abbrev") or "").strip(),
            category=(ri.get("category") or "").strip(),
            promulgated=parse_date(li.get("promulgation_date")),
            amended=parse_date(ri.get("amendment_promulgate_date")),
            enforced=parse_date(ri.get("amendment_enforcement_date")),
            amend_law_title=(ri.get("amendment_law_title") or "").strip(),
            amend_law_num=(ri.get("amendment_law_num") or "").strip(),
            repeal_status=ri.get("repeal_status") or "None",
            repeal_date=parse_date(ri.get("repeal_date")),
            revisions=[Revision.from_raw(r) for r in (raw.get("revisions") or [])],
            toc=list(raw.get("toc") or []),
            article1=raw.get("article1"),
            first_seen=parse_date(meta.get("first_seen")),
            fetched_at=parse_date(meta.get("fetched_at")),
        )

    # ---- 表示用の名前 ----
    @property
    def path(self) -> str:
        return f"law/{self.law_id}/"

    @property
    def egov_url(self) -> str:
        return f"https://laws.e-gov.go.jp/law/{self.law_id}"

    @property
    def type_label(self) -> str:
        return LAW_TYPES.get(self.law_type, ("その他", "misc"))[0]

    @property
    def type_slug(self) -> str:
        return LAW_TYPES.get(self.law_type, ("その他", "misc"))[1]

    @property
    def category_slug(self) -> str:
        return slug_of(CATEGORIES, self.category) if self.category else ""

    @property
    def ministries(self) -> list[str]:
        return ministries_of(self.law_num)

    def ministry_slug(self, name: str) -> str:
        return slug_of(MINISTRIES, name)

    @property
    def repeal_label(self) -> str:
        return REPEAL_LABELS.get(self.repeal_status, "")

    @property
    def is_repealed(self) -> bool:
        return self.repeal_status not in ("None", "", None)

    # ---- 日付の判定 ----
    def is_new_law(self, since: date) -> bool:
        """since 以降に公布された、新しく作られた法令。"""
        return bool(self.promulgated and self.promulgated >= since)

    @property
    def last_promulgated(self) -> date | None:
        """この法令について最後に公布があった日（新規公布日と改正法令の公布日の新しいほう）。"""
        ds = [d for d in (self.promulgated, self.amended) if d]
        return max(ds) if ds else None

    def enforcement_dates(self) -> list[date]:
        """この法令について分かっている施行日をすべて（過去の版・現行・施行予定）。古い順。

        `enforced` は現行の版の施行日しか指さないので、これだけで「今週の施行」を数えると
        今週のうち今日より後に施行される版を取りこぼす。そのため改正履歴も合わせて見る。
        """
        ds = {r.enforcement for r in self.revisions if r.enforcement}
        if self.enforced:
            ds.add(self.enforced)
        return sorted(ds)

    def promulgation_dates(self) -> list[date]:
        """この法令について分かっている公布日をすべて（法令そのものと、各改正法令）。古い順。"""
        ds = {r.promulgate for r in self.revisions if r.promulgate}
        for d in (self.promulgated, self.amended):
            if d:
                ds.add(d)
        return sorted(ds)

    def enforced_between(self, start: date, end: date) -> date | None:
        """start〜end に施行日があればその日を返す（複数あれば最初の 1 つ）。"""
        return next((d for d in self.enforcement_dates() if start <= d <= end), None)

    def promulgated_between(self, start: date, end: date) -> date | None:
        """start〜end に公布日があればその日を返す。"""
        return next((d for d in self.promulgation_dates() if start <= d <= end), None)

    def future_revisions(self, today: date) -> list[Revision]:
        """まだ施行されていない版を、施行日の近い順に返す。"""
        out = [r for r in self.revisions if r.is_future(today)]
        out.sort(key=lambda r: r.enforcement or date.max)
        return out

    def next_enforcement(self, today: date) -> date | None:
        fu = self.future_revisions(today)
        return fu[0].enforcement if fu else None

    def status(self, today: date) -> tuple[str, str]:
        """('scheduled'|'enforced'|'repealed', 表示ラベル)"""
        if self.is_repealed:
            return "repealed", self.repeal_label or "廃止"
        nxt = self.next_enforcement(today)
        if nxt:
            return "scheduled", f"{fmt_date(nxt)} 施行予定"
        if self.enforced and (today - self.enforced).days <= 30:
            return "enforced", "施行されたばかり"
        return "enforced", "施行中"

    def days_until(self, today: date) -> int | None:
        nxt = self.next_enforcement(today)
        return (nxt - today).days if nxt else None

    def is_new(self, today: date, days: int) -> bool:
        """このサイトで最近つかんだ動き（公布・施行）があるか。"""
        ds = [d for d in (self.last_promulgated, self.enforced) if d]
        return any(0 <= (today - d).days < days for d in ds)

    # ---- 本文の冒頭 ----
    @property
    def purpose_text(self) -> str:
        """第一条の本文をつなげたもの（目的条文）。"""
        if not self.article1:
            return ""
        return "\n".join(self.article1.get("paragraphs") or [])

    @property
    def purpose_caption(self) -> str:
        if not self.article1:
            return ""
        return (self.article1.get("caption") or "").strip()

    @property
    def summary_line(self) -> str:
        """一覧やメタ情報に出す 1 行。"""
        body = self.purpose_text.replace("\n", " ")
        if body:
            return body[:110] + ("…" if len(body) > 110 else "")
        return f"{self.law_num}。{self.category}に分類される{self.type_label}です。"


def fmt_date(d: date | None) -> str:
    return f"{d.year}年{d.month}月{d.day}日" if d else "記載なし"


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def week_range(today: date) -> tuple[date, date]:
    """today を含む週（月曜〜日曜）。"""
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


def next_month_range(today: date) -> tuple[date, date]:
    """翌月の初日と末日。"""
    y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    start = date(y, m, 1)
    y2, m2 = (y + 1, 1) if m == 12 else (y, m + 1)
    return start, date(y2, m2, 1) - timedelta(days=1)


def today_jst() -> date:
    return datetime.now(JST).date()
