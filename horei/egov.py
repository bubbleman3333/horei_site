"""e-Gov 法令 API（Version 2、デジタル庁）から法令データを取ってきて data/laws/ に貯める。

API はキー不要。https://laws.e-gov.go.jp/api/2/swagger-ui が仕様。

取り方の方針:
  1. 「未来の時点」で法令一覧を引くと、まだ施行されていない版が revision_info に入る。
     これで「施行予定のある法令」の ID を先に集める。
  2. 現時点で法令一覧を全件めくり、直近 1 年に公布・施行があったもの、または 1 で拾ったものを対象にする。
     （一覧 API の order は law_info の項目しか効かないので、絞り込みは手元でやる。）
  3. 対象ごとに改正履歴（/law_revisions）と、本文の冒頭（/law_data の目次と第一条）を取る。
     本文は丸ごと取ると数 MB になるので、elm で必要な要素だけを指定する。

毎日の更新では 3 の本文は「新しく増えた法令」と「データが更新された法令」だけ取り直す。
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from .model import JST, parse_date

API = "https://laws.e-gov.go.jp/api/2"
DATA_DIR = Path("data/laws")

WINDOW_DAYS = 365      # 「直近」とみなす日数（これより前の公布・施行は載せない）
FUTURE_DAYS = 550      # 施行予定をどこまで先読みするか
PAGE = 500             # 一覧 1 回あたりの件数
MAX_REVISIONS = 40     # 保存する改正履歴の件数
MAX_TOC = 60           # 保存する目次の行数
MAX_PARAGRAPHS = 4     # 保存する第一条の項の数
MAX_CHARS = 700        # 1 項あたりの文字数

# 第一条は法令によって置かれている深さが違う（章の下、編の下など）ので、上から順に試す
ARTICLE_ELMS = (
    "MainProvision-Article[1]",
    "MainProvision-Chapter_1-Article_1",
    "MainProvision-Chapter_1-Section_1-Article_1",
    "MainProvision-Part_1-Chapter_1-Article_1",
    "MainProvision-Paragraph[1]",
)

# revision_info からサイトで使う項目だけ残す（法令名の読みや改正法令の読みは使わない）
REVISION_KEYS = ("law_revision_id", "law_type", "law_title", "law_title_kana", "abbrev", "category", "updated",
                 "amendment_promulgate_date", "amendment_enforcement_date", "amendment_enforcement_comment",
                 "amendment_scheduled_enforcement_date", "amendment_law_id", "amendment_law_title",
                 "amendment_law_num", "amendment_type", "repeal_status", "repeal_date", "mission",
                 "current_revision_status")
# 改正履歴の 1 行に残す項目（法令名や分類は法令ごとに 1 つあれば足りるので持たない）
REVISION_ROW_KEYS = ("law_revision_id", "amendment_promulgate_date", "amendment_enforcement_date",
                     "amendment_enforcement_comment", "amendment_scheduled_enforcement_date",
                     "amendment_law_id", "amendment_law_title", "amendment_law_num", "amendment_type",
                     "current_revision_status")


class NotFound(Exception):
    """指定した要素が法令本文に無い（400021）。エラーではなく「無い」という答え。"""


_local = threading.local()


def _session() -> requests.Session:
    """スレッドごとに 1 本の Session を使い回す（requests の Session はスレッド共有に向かない）。"""
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = "horei-watch/1.0 (static site generator)"
        _local.session = s
    return s


def _get(session: requests.Session, url: str, params: dict | None = None, tries: int = 3) -> dict:
    """GET して JSON を返す。429（回数制限）は待って何度でもやり直す。400 の「要素が無い」は NotFound。"""
    last: Exception | None = None
    i = 0
    limited = 0
    while i < tries:
        try:
            r = session.get(url, params=params, timeout=90)
            if r.status_code == 429:
                limited += 1
                if limited > 30:
                    raise RuntimeError("429 が続くので諦めた")
                time.sleep(float(r.headers.get("Retry-After") or min(2 * limited, 20)))
                continue
            if r.status_code == 400:
                body = r.json()
                if str(body.get("code", "")).startswith("4000"):
                    raise NotFound(body.get("message", ""))
            r.raise_for_status()
            if not r.content:
                # 本文が大きすぎるときなど、200 で中身が空のことがある。無いものとして扱う
                raise NotFound("レスポンスが空")
            return r.json()
        except NotFound:
            raise
        except Exception as e:  # noqa: BLE001 - ネットワーク系は何でも再試行
            last = e
            i += 1
            time.sleep(2 * i)
    raise RuntimeError(f"法令 API に失敗: {url} {params}: {last}")


def page_laws(session: requests.Session, **params):
    """/laws を最後までめくって 1 件ずつ返す。"""
    offset = 0
    while True:
        data = _get(session, f"{API}/laws", {**params, "limit": PAGE, "offset": offset,
                                             "omit_current_revision_info": "true"})
        for row in data.get("laws", []):
            yield row
        nxt = data.get("next_offset")
        if not nxt:
            break
        offset = nxt


# ---- 本文の切り出し ----

def _texts(node) -> list[str]:
    """light 形式の JSON から文字列だけを順番に拾う。"""
    out: list[str] = []
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, list):
        for v in node:
            out += _texts(v)
    elif isinstance(node, dict):
        for v in node.values():
            out += _texts(v)
    return out


def extract_toc(full_text: dict | None) -> list[str]:
    """目次から章（無ければ節・条）の見出しを取り出す。"""
    toc = (full_text or {}).get("TOC")
    if not isinstance(toc, dict):
        return []
    rows: list[str] = []

    def walk(node, keys: tuple[str, ...]) -> None:
        if isinstance(node, list):
            for v in node:
                walk(v, keys)
        elif isinstance(node, dict):
            for k, v in node.items():
                if k in keys:
                    label = " ".join(t.strip() for t in _texts(v) if t.strip())
                    if label:
                        rows.append(label)
                else:
                    walk(v, keys)

    for keys in (("PartTitle", "ChapterTitle"), ("SectionTitle",), ("ArticleTitle",)):
        rows = []
        walk(toc, keys)
        if rows:
            break
    return rows[:MAX_TOC]


def extract_article(full_text: dict | None) -> dict | None:
    """第一条（条が無い法令は第一項）を {"caption","title","paragraphs"} にする。"""
    if not isinstance(full_text, dict):
        return None
    node = full_text.get("Article") or full_text.get("Paragraph")
    if isinstance(node, list):
        node = node[0] if node else None
    if not isinstance(node, dict):
        return None
    caption = " ".join(_texts(node.get("ArticleCaption"))).strip()
    title = " ".join(_texts(node.get("ArticleTitle"))).strip()
    paras = node.get("Paragraph")
    if paras is None:
        paras = [node]
    if isinstance(paras, dict):
        paras = [paras]
    out: list[str] = []
    for p in paras[:MAX_PARAGRAPHS]:
        if not isinstance(p, dict):
            continue
        text = "".join(t.strip() for t in _texts(p.get("ParagraphSentence") or p.get("Sentence") or p))
        text = text.strip()
        if text:
            out.append(text[:MAX_CHARS] + ("…" if len(text) > MAX_CHARS else ""))
    if not out:
        return None
    return {"caption": caption, "title": title or "第一条", "paragraphs": out}


def fetch_body(session: requests.Session, ref: str) -> tuple[list[str], dict | None]:
    """目次と第一条を取る。本文全体は取らない（法令によっては数 MB になるため）。

    `ref` は法令 ID でも法令履歴 ID でもよい。まだ施行されていない法令は、法令 ID だけで引くと
    本文が空で返るので、呼ぶ側で新しい版の法令履歴 ID を渡す。
    """
    common = {"response_format": "json", "law_full_text_format": "json", "json_format": "light",
              "omit_amendment_suppl_provision": "true"}
    toc: list[str] = []
    try:
        d = _get(session, f"{API}/law_data/{ref}", {**common, "elm": "TOC[1]"})
        toc = extract_toc(d.get("law_full_text"))
    except (NotFound, RuntimeError):
        # 目次が無い法令も、本文が大きすぎて返ってこない法令もある。どちらも「目次なし」でよい
        pass
    article = None
    for elm in ARTICLE_ELMS:
        try:
            d = _get(session, f"{API}/law_data/{ref}", {**common, "elm": elm})
        except (NotFound, RuntimeError):
            continue
        article = extract_article(d.get("law_full_text"))
        if article:
            break
    return toc, article


def fetch_revisions(session: requests.Session, law_id: str) -> list[dict]:
    """改正履歴。新しい順で返ってくるので、そのまま上から必要な数だけ残す。"""
    d = _get(session, f"{API}/law_revisions/{law_id}")
    rows = d.get("revisions") or []
    return [{k: r.get(k) for k in REVISION_ROW_KEYS if r.get(k) is not None} for r in rows[:MAX_REVISIONS]]


# ---- 同期 ----

def is_target(row: dict, since: date) -> bool:
    """直近 1 年に公布または施行があったか。"""
    li, ri = row.get("law_info") or {}, row.get("revision_info") or {}
    for value in (li.get("promulgation_date"), ri.get("amendment_promulgate_date"), ri.get("amendment_enforcement_date")):
        d = parse_date(value)
        if d and d >= since:
            return True
    return False


def sync(data_dir: Path = DATA_DIR, *, window_days: int = WINDOW_DAYS, limit: int | None = None,
         sleep: float = 0.02, workers: int = 8, log=print) -> tuple[int, int]:
    """法令 API から取り込んで data/laws/ を更新する。戻り値は (新規件数, 更新件数)。

    1 件あたり数リクエストいるので、詳細取得だけ `workers` 本のスレッドで並べる
    （API 側に回数制限は見当たらないが、429 が来たら待って再試行する）。
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    session = _session()
    today = datetime.now(JST).date()
    since = today - timedelta(days=window_days)

    # 1. 未来の時点で一覧を引き、施行予定のある法令の ID を集める
    asof = (today + timedelta(days=FUTURE_DAYS)).isoformat()
    future_ids: set[str] = set()
    for row in page_laws(session, asof=asof):
        enf = parse_date((row.get("revision_info") or {}).get("amendment_enforcement_date"))
        if enf and enf > today:
            future_ids.add((row.get("law_info") or {}).get("law_id") or "")
    future_ids.discard("")
    log(f"施行予定のある法令: {len(future_ids)} 件（{asof} まで先読み）")

    # 2. 現時点の一覧から対象を決める
    targets: dict[str, dict] = {}
    scanned = 0
    for row in page_laws(session):
        scanned += 1
        law_id = (row.get("law_info") or {}).get("law_id") or ""
        if not law_id:
            continue
        if law_id in future_ids or is_target(row, since):
            ri = row.get("revision_info") or {}
            targets[law_id] = {"law_info": row.get("law_info"),
                               "revision_info": {k: ri.get(k) for k in REVISION_KEYS if k in ri}}
    log(f"全 {scanned} 件をめくって、対象 {len(targets)} 件")

    if limit is not None:
        targets = dict(list(targets.items())[:limit])
        log(f"--limit により {len(targets)} 件に絞った")

    # 3. 対象ごとに改正履歴と本文の冒頭を取る
    now = today.isoformat()
    counts = {"new": 0, "updated": 0, "done": 0, "failed": 0}
    lock = threading.Lock()
    total = len(targets)

    def fetch_one(item: tuple[str, dict]) -> None:
        law_id, row = item
        session = _session()
        path = data_dir / f"{law_id}.json"
        before = path.read_text(encoding="utf-8") if path.exists() else None
        old = json.loads(before) if before else None
        old_meta = (old or {}).get("_meta") or {}
        stamp = (row["revision_info"] or {}).get("updated") or ""
        current_rev = (row["revision_info"] or {}).get("law_revision_id")

        # 改正履歴は施行予定が増減するので毎回取り直す（1 件 1 リクエストで軽い）
        revisions = fetch_revisions(session, law_id)
        time.sleep(sleep)

        # 本文は初回と、e-Gov 側でデータが更新されたときだけ取り直す
        if old and old_meta.get("updated") == stamp and (old.get("toc") or old.get("article1")):
            toc, article = old.get("toc") or [], old.get("article1")
        else:
            toc, article = fetch_body(session, current_rev or law_id)
            # まだ施行されていない法令は現時点の版の本文が空なので、いちばん新しい版で取り直す
            if article is None and revisions:
                newest = revisions[0].get("law_revision_id")
                if newest and newest != current_rev:
                    toc2, article = fetch_body(session, newest)
                    toc = toc or toc2
            time.sleep(sleep)

        first_seen = old_meta.get("first_seen")
        if not first_seen:
            # 初回の一括取り込みで全部が「新着」にならないよう、公布日が過去ならそれを使う
            base = parse_date((row["law_info"] or {}).get("promulgation_date"))
            first_seen = base.isoformat() if base and base.isoformat() < now else now
        # fetched_at は「中身が変わった日」。毎日書き換えると全ファイルが git の差分になってしまうので、
        # まず前回の値のまま組み立てて、中身が同じならファイルに触らない。
        out = {**row, "revisions": revisions, "toc": toc, "article1": article,
               "_meta": {"first_seen": first_seen, "fetched_at": old_meta.get("fetched_at") or now, "updated": stamp}}
        text = json.dumps(out, ensure_ascii=False, indent=1)
        changed = text != before
        if changed:
            out["_meta"]["fetched_at"] = now
            text = json.dumps(out, ensure_ascii=False, indent=1)
            path.write_text(text, encoding="utf-8")
        with lock:
            if before is None:
                counts["new"] += 1
            elif changed:
                counts["updated"] += 1
            counts["done"] += 1
            if counts["done"] % 200 == 0:
                log(f"  {counts['done']}/{total}")

    def work(item: tuple[str, dict]) -> None:
        """1 件の失敗で全体を止めない。落ちた法令は次回の取り込みでまた拾われる。"""
        try:
            fetch_one(item)
        except Exception as e:  # noqa: BLE001
            with lock:
                counts["failed"] += 1
                counts["done"] += 1
                if counts["failed"] <= 20:
                    log(f"  取得できず: {item[0]}: {e}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, targets.items()))
    if counts["failed"]:
        log(f"取得できなかった法令: {counts['failed']} 件")
    new, updated = counts["new"], counts["updated"]

    # 4. 対象から外れた法令（1 年以上動きが無くなった）は消す
    removed = 0
    for path in data_dir.glob("*.json"):
        if path.stem not in targets and limit is None:
            path.unlink()
            removed += 1
    log(f"新規 {new} 件、更新 {updated} 件、削除 {removed} 件")
    return new, updated
