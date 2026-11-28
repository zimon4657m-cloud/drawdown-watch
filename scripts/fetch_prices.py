"""watchlist.json の銘柄と指数(^N225, ^GSPC)を yfinance で取得し data/prices.json に保存する。

- 最高値は終値ベース。期間 1y/3y/5y/max ごとに算出
- 指数は直近5年の日次終値(グラフ用)も保存
- 取得に失敗した銘柄は前回の値を残す(失敗時は数回やり直す)
- 名前・EPS・BPS・配当(info)は取得に時間がかかるので、前回から7日以上たった銘柄だけ取り直す
- 過去5年の増配率(dg5)は配当の履歴から計算する(価格と同じ取得なので追加の通信なし)
- 指数が2つとも取れなければ保存しない(終了コード1)
"""
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST = ROOT / "watchlist.json"
OUT = ROOT / "data" / "prices.json"
INDICES = {"^N225": "日経平均", "^GSPC": "S&P500"}
PERIODS = {"1y": 365, "3y": 365 * 3, "5y": 365 * 5}
JST = timezone(timedelta(hours=9))
INFO_DAYS = 7      # info を取り直す間隔(日)
RETRY = 3
PAUSE = 0.3        # 銘柄ごとの待ち時間(秒)。取得先に負担をかけすぎないため
INFO_KEYS = ("name", "currency", "eps", "bps", "dps")


def num(v):
    try:
        v = float(v)
        return None if math.isnan(v) or math.isinf(v) else v
    except (TypeError, ValueError):
        return None


def retry(fn, *a, **k):
    for i in range(RETRY):
        try:
            return fn(*a, **k)
        except Exception:
            if i == RETRY - 1:
                raise
            time.sleep(2 * (i + 1))


def div_growth(hist, first_date, years=5):
    """過去5年の増配率(年平均)。直近の確定した年(去年)の年間配当と、その5年前の年間配当から計算する。
    今年はまだ配当が出そろっていないので使わない。データが足りない・無配の年がある場合は None"""
    if "Dividends" not in hist:
        return None
    d = hist["Dividends"].dropna()
    d = d[d > 0]
    if d.empty:
        return None
    by_year = d.groupby(d.index.year).sum()
    last = datetime.now(JST).year - 1
    base = last - years
    if first_date.year >= base:  # 基準の年がまるごと入っていない
        return None
    a, b = by_year.get(base, 0), by_year.get(last, 0)
    if a <= 0 or b <= 0:
        return None
    return round((b / a) ** (1 / years) - 1, 4)


def fetch(symbol, with_series=False, need_info=True):
    t = yf.Ticker(symbol)
    hist = t.history(period="max", auto_adjust=False)
    if hist is None or hist.empty or "Close" not in hist:
        raise RuntimeError("価格データが空")
    close = hist["Close"].dropna()
    if close.empty:
        raise RuntimeError("終値が空")
    last_date = close.index[-1]
    highs = {}
    for key, days in PERIODS.items():
        s = close[close.index >= last_date - timedelta(days=days)]
        highs[key] = {"value": round(float(s.max()), 4), "date": s.idxmax().strftime("%Y-%m-%d")}
    highs["max"] = {"value": round(float(close.max()), 4), "date": close.idxmax().strftime("%Y-%m-%d")}
    rec = {
        "price": round(float(close.iloc[-1]), 4),
        "date": last_date.strftime("%Y-%m-%d"),
        "first_date": close.index[0].strftime("%Y-%m-%d"),
        "highs": highs,
    }
    if with_series:
        s = close[close.index >= last_date - timedelta(days=365 * 5)]
        rec["series"] = [[d.strftime("%Y-%m-%d"), round(float(v), 2)] for d, v in s.items()]
        return rec
    rec["dg5"] = num(div_growth(hist, close.index[0]))
    if need_info:
        info = {}
        try:
            info = retry(lambda: t.info) or {}
        except Exception as e:  # info は失敗しやすいので価格だけでも残す
            print(f"  info取得失敗 {symbol}: {e}")
        rec["name"] = info.get("shortName") or info.get("longName")
        rec["currency"] = info.get("currency")
        rec["eps"] = num(info.get("trailingEps"))
        rec["bps"] = num(info.get("bookValue"))
        rec["dps"] = num(info.get("dividendRate"))
        rec["info_date"] = datetime.now(JST).strftime("%Y-%m-%d")
    return rec


def main():
    watch = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    prev = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = {}
    prev_idx = prev.get("indices") or {}
    prev_stk = prev.get("stocks") or {}

    indices, ok_idx = {}, 0
    for sym, name in INDICES.items():
        try:
            rec = retry(fetch, sym, with_series=True)
            rec["name"] = name
            indices[sym] = rec
            ok_idx += 1
            print(f"OK {sym} {rec['price']} ({rec['date']})")
        except Exception as e:
            print(f"NG {sym}: {e}")
            if sym in prev_idx:
                indices[sym] = {**prev_idx[sym], "stale": True}
    if ok_idx == 0:
        print("指数が2つとも取得できなかったため保存しません")
        sys.exit(1)

    stocks, n_info = {}, 0
    today = datetime.now(JST).date()
    for item in watch.get("stocks", []):
        sym = item["symbol"]
        old = prev_stk.get(sym) or {}
        try:
            fresh = datetime.strptime(old.get("info_date", ""), "%Y-%m-%d").date() > today - timedelta(days=INFO_DAYS)
        except ValueError:
            fresh = False
        try:
            rec = retry(fetch, sym, need_info=not fresh)
            if fresh:  # 前回の info をそのまま使う
                for k in INFO_KEYS + ("info_date",):
                    if k in old:
                        rec[k] = old[k]
            else:
                n_info += 1
            print(f"OK {sym} {rec['price']} ({rec['date']})")
        except Exception as e:
            print(f"NG {sym}: {e}")
            if sym not in prev_stk:
                continue
            rec = {**prev_stk[sym], "stale": True}
        # watchlist.json の手入力値を優先
        for k in ("name", "eps", "bps", "dps"):
            if item.get(k) not in (None, ""):
                rec[k] = item[k]
        for k in ("group", "alias"):  # 検索・分類用
            if item.get(k):
                rec[k] = item[k]
        stocks[sym] = rec
        time.sleep(PAUSE)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    out = {"updated": datetime.now(JST).isoformat(timespec="minutes"), "indices": indices, "stocks": stocks}
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"保存: {OUT} (指数{ok_idx}/2, 銘柄{len(stocks)}/{len(watch.get('stocks', []))}, info取り直し{n_info})")


if __name__ == "__main__":
    main()
