"""使い方:
  python -m horei fetch [--limit N]                       e-Gov 法令 API から取り込む
  python -m horei build [--out dist] [--site-url URL]     サイトを生成
  python -m horei all                                      fetch → build
  python -m horei pending [--limit N] [--json]             解説がまだ無い法令を出す
  python -m horei digest [法令ID ...]                      解説を書くための要約テキストを出す
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .articles import pending
from .build import ROOT, Builder, load_config, load_laws
from .egov import sync
from .model import JST, Law, fmt_date


def digest(x: Law, today) -> str:
    """1 件分を解説執筆用の短いテキストにする。"""
    lines = [
        f"=== {x.law_id}",
        f"title: {x.title}（{x.title_kana}）" + (f" 略称 {x.abbrev}" if x.abbrev else ""),
        f"law_num: {x.law_num}   type: {x.type_label}   category: {x.category}",
        f"ministries: {'・'.join(x.ministries) or '-'}",
        f"promulgated: {fmt_date(x.promulgated)}   enforced: {fmt_date(x.enforced)}",
        f"last_amendment: {x.amend_law_title or '-'}（{x.amend_law_num or '-'}）",
        f"egov: {x.egov_url}",
        "future:",
    ]
    for r in x.future_revisions(today)[:8]:
        lines.append(f"  {fmt_date(r.enforcement)}  {r.law_title}（{r.law_num}）{r.comment}")
    lines.append("toc: " + " / ".join(x.toc[:20]))
    lines.append("article1: " + x.purpose_text.replace("\n", " ")[:1200])
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="horei", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--limit", type=int, default=None, help="詳細を取る件数の上限（試し用）")
    f.add_argument("--workers", type=int, default=8, help="同時に投げるリクエストの本数")
    b = sub.add_parser("build")
    b.add_argument("--out", default="dist")
    b.add_argument("--site-url", default=None)
    a = sub.add_parser("all")
    a.add_argument("--out", default="dist")
    a.add_argument("--site-url", default=None)
    a.add_argument("--workers", type=int, default=8)
    q = sub.add_parser("pending")
    q.add_argument("--limit", type=int, default=None)
    q.add_argument("--json", action="store_true", help="法令 ID だけを JSON 配列で出す")
    d = sub.add_parser("digest", help="解説を書くための要約テキストを出す（Claude が読む用）")
    d.add_argument("ids", nargs="*", help="法令 ID。省略すると pending の先頭から")
    d.add_argument("--limit", type=int, default=20)
    d.add_argument("--out", default=None, help="書き出すファイル（省略時は標準出力）")
    args = p.parse_args(argv)

    site, _ = load_config()
    data_dir = ROOT / "data" / "laws"
    today = datetime.now(JST).date()

    if args.cmd in ("fetch", "all"):
        sync(data_dir, limit=getattr(args, "limit", None), workers=args.workers)
    if args.cmd in ("build", "all"):
        n = Builder(Path(args.out), args.site_url or site["site_url"]).build()
        print(f"{n} 件の法令から {args.out}/ を生成した")
    if args.cmd == "pending":
        rows = pending(load_laws(data_dir), today)
        if args.limit:
            rows = rows[: args.limit]
        if args.json:
            print(json.dumps([x.law_id for x in rows], ensure_ascii=False))
        else:
            for x in rows:
                nxt = x.next_enforcement(today)
                print(f"{x.law_id}\t{nxt.isoformat() if nxt else '----------'}\t{x.type_label}\t{x.title}")
            print(f"合計 {len(rows)} 件")
    if args.cmd == "digest":
        laws = load_laws(data_dir)
        if args.ids:
            by_id = {x.law_id: x for x in laws}
            rows = [by_id[i] for i in args.ids if i in by_id]
        else:
            rows = pending(laws, today)[: args.limit]
        text = "\n".join(digest(x, today) for x in rows)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"{len(rows)} 件を {args.out} に書いた")
        else:
            sys.stdout.reconfigure(encoding="utf-8")
            print(text)


if __name__ == "__main__":
    main()
