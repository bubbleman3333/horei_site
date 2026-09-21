"""法令ページの本文の組み立て。

data/articles/<法令ID>.md に人（Claude）が書いた解説があればそれを使い、無ければデータから自動で組み立てる。
どちらも {"title", "summary", "body_html", "written"} の形で返す。

自動文では「データから読み取れること」しか書かない。法令の解釈や助言は書かない。
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import markdown

from .model import Law, fmt_date

ARTICLE_DIR = Path("data/articles")


def parse_markdown_article(text: str) -> dict:
    """先頭の '---' で囲んだ frontmatter（title / summary）と Markdown 本文に分ける。"""
    meta: dict[str, str] = {}
    body = text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"')
        body = text[m.end():]
    html = markdown.markdown(body, extensions=["tables", "sane_lists"])
    return {"title": meta.get("title", ""), "summary": meta.get("summary", ""), "body_html": html, "written": True}


def load_article(law_id: str, article_dir: Path = ARTICLE_DIR) -> dict | None:
    path = article_dir / f"{law_id}.md"
    if not path.exists():
        return None
    return parse_markdown_article(path.read_text(encoding="utf-8"))


def auto_summary(law: Law, today: date) -> str:
    """検索結果や OGP に出る 1 行。"""
    nxt = law.next_enforcement(today)
    parts = [f"{law.title}（{law.law_num}）の改正履歴と施行日。"]
    if law.enforced:
        parts.append(f"いまの条文は{fmt_date(law.enforced)}施行。")
    if nxt:
        parts.append(f"{fmt_date(nxt)}に次の改正が施行されます。")
    if law.category:
        parts.append(f"分野は「{law.category}」。")
    return "".join(parts)


def esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def auto_article(law: Law, today: date) -> dict:
    """解説が書かれていないときの穴埋め。"""
    parts: list[str] = []

    parts.append("<h2>この法令は何を定めているか</h2>")
    if law.purpose_text:
        cap = f"{law.purpose_caption}" if law.purpose_caption else ""
        parts.append(f"<p>{esc(law.title)}の{esc(law.article1.get('title', '第一条'))}{esc(cap)}は、次のように定めています。</p>")
        for p in law.article1.get("paragraphs", []):
            parts.append(f"<blockquote><p>{esc(p)}</p></blockquote>")
        parts.append(f'<p class="source">出典: e-Gov 法令検索（デジタル庁）'
                     f'<a href="{law.egov_url}" rel="noopener" target="_blank">{esc(law.title)}</a>'
                     f'（{fmt_date(law.fetched_at)} 取得）</p>')
    else:
        parts.append(f"<p>{esc(law.title)}（{esc(law.law_num)}）は、e-Gov 法令検索で「{esc(law.category)}」に"
                     f"分類されている{law.type_label}です。目的を定めた条文は取得できていません。"
                     f'全文は<a href="{law.egov_url}" rel="noopener" target="_blank">e-Gov 法令検索</a>で読めます。</p>')

    parts.append("<h2>最近の動き</h2>")
    moves: list[str] = []
    if law.promulgated:
        moves.append(f"<li>公布: {fmt_date(law.promulgated)}（{esc(law.law_num)}）</li>")
    if law.enforced:
        label = "いまの条文の施行日"
        moves.append(f"<li>{label}: {fmt_date(law.enforced)}</li>")
    if law.amend_law_title:
        moves.append(f"<li>直近の改正: {esc(law.amend_law_title)}"
                     + (f"（{esc(law.amend_law_num)}、{fmt_date(law.amended)}公布）" if law.amend_law_num else "")
                     + "</li>")
    if law.is_repealed:
        moves.append(f"<li>この法令は{esc(law.repeal_label)}しています（{fmt_date(law.repeal_date)}）</li>")
    parts.append("<ul>" + "".join(moves) + "</ul>")

    future = law.future_revisions(today)
    if future:
        parts.append("<h2>これから施行されること</h2><ul>")
        for r in future[:6]:
            left = (r.enforcement - today).days if r.enforcement else None
            tail = f"（あと{left}日）" if left is not None and left >= 0 else ""
            note = f"　{esc(r.comment)}" if r.comment else ""
            parts.append(f"<li><strong>{fmt_date(r.enforcement)}{tail}</strong>　{esc(r.law_title or '改正')}"
                         + (f"（{esc(r.law_num)}）" if r.law_num else "") + note + "</li>")
        parts.append("</ul>")
        parts.append("<p>施行日は政令で後から決まることがあります。確定した日付かどうかは e-Gov 法令検索で確認してください。</p>")
    else:
        parts.append("<h2>これから施行されること</h2>"
                     "<p>いまのところ、施行日が決まっている改正はありません。</p>")

    if law.toc:
        parts.append("<h2>目次</h2><ul class=\"toc\">")
        for row in law.toc:
            parts.append(f"<li>{esc(row)}</li>")
        parts.append("</ul>")

    return {"title": law.title, "summary": auto_summary(law, today), "body_html": "\n".join(parts), "written": False}


def article_for(law: Law, today: date, article_dir: Path = ARTICLE_DIR) -> dict:
    a = load_article(law.law_id, article_dir)
    if a is None:
        return auto_article(law, today)
    if not a["title"]:
        a["title"] = law.title
    if not a["summary"]:
        a["summary"] = auto_summary(law, today)
    return a


def pending(laws: list[Law], today: date, article_dir: Path = ARTICLE_DIR) -> list[Law]:
    """解説がまだ書かれていない法令。これから施行されるものを近い順に、次に最近施行されたものを返す。"""
    out = [x for x in laws if not (article_dir / f"{x.law_id}.md").exists()]

    def key(x: Law) -> tuple[int, int]:
        nxt = x.next_enforcement(today)
        if nxt:
            return (0, nxt.toordinal())
        return (1, -(x.enforced or date.min).toordinal())
    out.sort(key=key)
    return out
