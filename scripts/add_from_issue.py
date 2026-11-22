"""Issue のタイトル「add: KO 7203.T」から銘柄を watchlist.json に追加する。

タイトルは環境変数 ISSUE_TITLE で受け取る(シェルに直接埋め込まない)。
追加した銘柄を標準出力に1行ずつ出す。
"""
import json
import os
import re
import sys
from pathlib import Path

WATCHLIST = Path(__file__).resolve().parent.parent / "watchlist.json"
SYM = re.compile(r"^[A-Z0-9.\-^=]{1,15}$")


def norm(s):
    s = s.strip().upper()
    return s + ".T" if re.fullmatch(r"\d[0-9A-Z]{3}", s) else s


def main():
    title = os.environ.get("ISSUE_TITLE", "")
    m = re.match(r"^\s*add\s*:\s*(.+)$", title, re.I)
    if not m:
        print("タイトルが「add: ティッカー」の形式ではありません", file=sys.stderr)
        sys.exit(1)
    syms = [norm(s) for s in re.split(r"[\s,、]+", m.group(1)) if s.strip()]
    bad = [s for s in syms if not SYM.match(s)]
    if bad or not syms:
        print(f"使えない文字を含むティッカー: {bad}", file=sys.stderr)
        sys.exit(1)

    w = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    have = {x["symbol"] for x in w.get("stocks", [])}
    added = [s for s in syms if s not in have]
    for s in added:
        w["stocks"].append({"symbol": s})
    WATCHLIST.write_text(json.dumps(w, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for s in added:
        print(s)


if __name__ == "__main__":
    main()
