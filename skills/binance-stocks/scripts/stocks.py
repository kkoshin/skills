#!/usr/bin/env python3
"""币安股票代币（EQ_*）持仓与技术指标查询。

用法:
  python3 stocks.py                 持仓 + 行情 + 盈亏（默认）
  python3 stocks.py kdj             持仓标的的日K KDJ（两种口径对比）
  python3 stocks.py kdj KO          指定标的
  python3 stocks.py quote KO        单只实时买卖盘
  python3 stocks.py orders          全部成交明细
  python3 stocks.py buy  TSLA --usd 100            建仓（只预览, 打印确认码）
  python3 stocks.py buy  TSLA --usd 100 --confirm <码>   真正下单
  python3 stocks.py sell KO --all                  平仓（同样两步确认）
  任意命令加 --json                 机器可读输出

需要的环境变量: BINANCE_API_KEY, BINANCE_SECRET_KEY
"""
import argparse
import datetime
import json
import math
import os
import secrets
import sys
import time
from collections import defaultdict

from binance_client import Client, BinanceError

DAY_MS = 86400000
FAPI_BASE = os.environ.get("BINANCE_FUTURES_API_BASE", "https://fapi.binance.com")

# 成交流水分页 (实测): 页码参数是 `current`, 每页条数是 `size`,
# size 上限 100(101 起报 -1102)。`page`/`pageNum`/`pageNo`/`limit` 都不被识别,
# 传了会被**静默忽略**——回显里的 page/size 仍是默认的 1/20。
PAGE_SIZE = 100
MAX_PAGES = 200                # 兜底, 防"翻页没生效"时死循环


# ---------------------------------------------------------------- 持仓
# 资金账户里的现金类资产，不该被当成股票代币
CASH_ASSETS = {"USDC", "USDT", "BUSD", "FDUSD", "DAI", "TUSD"}

_BSTOCK_CACHE_TTL_MS = 7 * 24 * 3600 * 1000


def _bstock_cache_path():
    return os.path.join(os.path.dirname(_cache_path()), "bstocks.json")


def fetch_bstocks(cli, force=False):
    """bStocks 资产名集合 —— 「这个资产是 bStock」的**直接证据**。

    权威来源是 /sapi/v1/capital/config/getall，判据为资产名以 "(bStocks)" 结尾
    （实测 77 个，如 `BABAB` = "Alibaba (bStocks)"、`MUUB` = "Direxion MU Bull
    2X ETF (bStocks)"）。这比按 "<代码>B" 去猜可靠：后者会把 `ACB`、`BRK.B`
    这类真实代码误伤。

    列表变化很慢，缓存 7 天 —— 这个接口一次吐 742 个资产，每次查持仓都拉太重。

    **拿不到时返回 None，而不是空集合** —— 两者必须分开：「确认没有 bStocks」
    和「没查到 bStocks」看起来都是空，但后者会让 bStock 持仓被静默跳过，
    正是这次要修的失败模式。调用方见到 None 必须出声。
    """
    p = _bstock_cache_path()
    if not force:
        try:
            with open(p) as f:
                d = json.load(f)
            if int(time.time() * 1000) - int(d.get("at", 0)) < _BSTOCK_CACHE_TTL_MS:
                return set(d.get("assets") or [])
        except Exception:
            pass
    try:
        cfg = cli.request("GET", "/sapi/v1/capital/config/getall")
        assets = [c["coin"] for c in cfg
                  if c.get("coin") and str(c.get("name", "")).endswith("(bStocks)")]
    except Exception:
        return None
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            json.dump({"at": int(time.time() * 1000), "assets": sorted(assets)}, f)
    except Exception:
        pass
    return set(assets)


def row_ticker(r, bstocks):
    """资金账户的一行资产 -> 交易符号。认不出返回 None。

    实测两种形态（都真实持有过）：
      - `EQ_<代码>`：币安内部账本资产，行里带 `stockTicker` 字段（EQ_MUU -> MUU）
      - `<代码>B`  ：bStocks 链上代币（BABAB -> BABA、MUUB -> MUU）
    **认不出就返回 None，绝不硬猜** —— 把别的资产当成股票，比看不见它更糟。
    """
    st = r.get("stockTicker")
    if st:
        return str(st)
    a = str(r.get("asset") or "")
    if a.startswith("EQ_"):
        return a[3:] or None
    if bstocks and a in bstocks:
        return a[:-1] or None
    return None


def fetch_holdings(cli):
    """返回 {交易符号: 数量}，**两种形态合并计算**。

    股票代币存在【资金账户】里，不是现货账户（/sapi/v3/asset/getUserAsset
    不返回 EQ_*，必须用 funding-asset，且是 POST）。

    早先这里只认 `EQ_` 前缀，结果一笔买 BABA 的单子落到 `BABAB`（bStock）
    之后，整个持仓在报表里凭空消失 —— 看不见，也就卖不掉。
    """
    rows = cli.request("POST", "/sapi/v1/asset/get-funding-asset")
    bs = fetch_bstocks(cli)
    out = defaultdict(float)
    for r in rows:
        t = row_ticker(r, bs)
        if not t:
            continue
        free = float(r.get("free", 0) or 0)
        if free > 0:
            out[t] += free
    if bs is None:
        # 名单拿不到时, 非 EQ_ 的资产分不出是股票代币还是普通币。**必须出声** ——
        # 否则 bStock 持仓又会像那次 BABA 一样无声消失。
        skipped = [str(r.get("asset")) for r in rows
                   if float(r.get("free", 0) or 0) > 0
                   and not str(r.get("asset") or "").startswith(("EQ_",))
                   and str(r.get("asset")) not in CASH_ASSETS]
        if skipped:
            print(f"  ⚠ 拿不到 bStocks 名单，无法判断这些资产是否为股票代币，已跳过: "
                  f"{'、'.join(skipped)}", file=sys.stderr)
    return dict(out)


def fetch_quote(cli, symbol):
    """取实时盘口。错误如实抛出，不吞 —— 否则调用方分不清是标的不存在、
    鉴权失败还是网络抖动，而这三种该采取的动作完全不同。"""
    return cli.request("GET", "/sapi/v1/equity/market/quote", {"symbol": symbol})


def fetch_orders(cli, days=4000):
    """取**全部**成交流水。

    这个接口分页, 而参数名不直观: 页码是 `current`, 每页条数是 `size`(上限 100)。
    之前传的是 `limit=500` —— 不被识别, 于是每次只拿回默认的 20 条, 流水一多就把
    最老的几笔悄悄丢掉。丢的若是某标的的建仓买单, 回放时对应的卖单就成了孤儿:
    该标的的已实现盈亏会整个消失(实测: 亏损会被算成盈利), 而且会随着
    新订单在窗口边缘进出而**反复变化**。

    所以这里翻页取全, 并且**取不全就报错** —— 宁可不显示, 也不能显示一个错的数。
    """
    now = int(time.time() * 1000)
    win = {"startTime": now - days * DAY_MS, "endTime": now}
    rows, seen, total, cur = [], set(), None, 1
    while cur <= MAX_PAGES:
        resp = cli.request("GET", "/sapi/v1/equity/order/history",
                           {**win, "current": cur, "size": PAGE_SIZE})
        if not isinstance(resp, dict):
            raise BinanceError(0, {"msg": f"成交流水返回了非预期响应: {resp!r}"})
        batch = resp.get("rows") or []
        total = int(resp.get("total") or 0) or total
        # 翻页参数若失效, 每一页都会返回同一批。当场发现并报错, 而不是死循环、
        # 也不是把重复数据当成交流水去回放。
        fresh = [r for r in batch
                 if (r.get("orderId") or r.get("clientOrderId")) not in seen]
        if batch and not fresh:
            raise BinanceError(0, {"msg": (
                f"成交流水分页没有生效: 第 {cur} 页与之前返回了完全相同的数据。"
                "此时无法保证流水完整, 拒绝据此计算已实现盈亏。")})
        seen.update(r.get("orderId") or r.get("clientOrderId") for r in batch)
        rows.extend(fresh)
        if not batch or (total and len(rows) >= total):
            break
        cur += 1
    if total and len(rows) < total:
        raise BinanceError(0, {"msg": (
            f"成交流水没取全: 接口报 total={total}, 实际只拿到 {len(rows)} 笔。"
            "少一笔建仓单就可能让某个标的的已实现盈亏整个消失, 因此拒绝计算。")})
    return sorted(rows, key=lambda r: r["createdAt"])


def replay(orders):
    """按移动加权成本法回放成交流水，得到成本基准与已实现盈亏。

    用成交明细自己算，不依赖接口的汇总字段 —— 那些字段在部分接口上口径不一致。
    """
    pos = defaultdict(lambda: {"qty": 0.0, "cost": 0.0})
    realized = defaultdict(float)
    fees = 0.0
    for o in orders:
        sym = o["symbol"]
        qty = float(o.get("filledQty") or 0)
        amt = float(o.get("filledTotal") or 0)
        fees += float(o.get("fee") or 0)
        if qty <= 0:
            continue
        p = pos[sym]
        if o["side"] == "BUY":
            p["qty"] += qty
            p["cost"] += amt
        elif p["qty"] > 0:
            avg = p["cost"] / p["qty"]
            take = min(qty, p["qty"])
            realized[sym] += amt - avg * take
            p["qty"] -= take
            p["cost"] -= avg * take
    return pos, realized, fees


def build_report(cli):
    orders = fetch_orders(cli)
    pos, realized, fees = replay(orders)
    holdings = fetch_holdings(cli)

    positions, cost_total = [], 0.0
    for sym in sorted(set(holdings) | {s for s, p in pos.items() if p["qty"] > 0}):
        qty = holdings.get(sym, 0.0)
        if qty <= 0:
            continue
        p = pos[sym]
        avg = p["cost"] / p["qty"] if p["qty"] > 0 else 0.0
        try:
            q = fetch_quote(cli, sym)
        except BinanceError as e:
            print(f"  ⚠ {sym} 取行情失败，该标的按 0 计价: {e}", file=sys.stderr)
            q = {}
        bid, ask = float(q.get("bidPrice") or 0), float(q.get("askPrice") or 0)
        last = bid or ask          # 卖出看 bid，没有 bid 就退回 ask
        mv = last * qty
        basis = avg * qty
        cost_total += basis
        positions.append({
            "symbol": sym, "quantity": round(qty, 8),
            "avgCost": round(avg, 6), "bid": bid, "ask": ask,
            "marketValue": round(mv, 2),
            "unrealizedPnl": round(mv - basis, 2),
            "unrealizedPct": round((mv - basis) / basis * 100, 2) if basis else 0.0,
        })

    realized_clean = {k: round(v, 2) for k, v in realized.items() if abs(v) > 1e-9}
    mv_total = sum(p["marketValue"] for p in positions)
    unr_total = sum(p["unrealizedPnl"] for p in positions)
    realized_total = sum(realized_clean.values())

    return {
        "positions": positions,
        "totals": {
            "marketValue": round(mv_total, 2),
            "costBasis": round(cost_total, 2),
            "unrealizedPnl": round(unr_total, 2),
            "unrealizedPct": round(unr_total / cost_total * 100, 2) if cost_total else 0.0,
            "realizedPnl": round(realized_total, 2),
            "fees": round(fees, 2),
            "netPnl": round(unr_total + realized_total - fees, 2),
        },
        "realizedBySymbol": dict(sorted(realized_clean.items(), key=lambda kv: kv[1])),
    }


def render(rep):
    pos, t = rep["positions"], rep["totals"]
    if not pos:
        print("当前没有股票代币持仓。")
    else:
        print("─" * 74)
        print(f"{'代码':<7}{'数量':>14}{'成本价':>11}{'现价':>10}{'市值':>10}{'浮盈':>9}{'浮盈%':>9}")
        print("─" * 74)
        for p in pos:
            print(f"{p['symbol']:<7}{p['quantity']:>14.8f}{p['avgCost']:>11.4f}"
                  f"{p['bid']:>10.2f}{p['marketValue']:>10.2f}"
                  f"{p['unrealizedPnl']:>+9.2f}{p['unrealizedPct']:>+8.2f}%")
        print("─" * 74)
        print(f"{'合计':<7}{'':>14}{'':>11}{'':>10}{t['marketValue']:>10.2f}"
              f"{t['unrealizedPnl']:>+9.2f}{t['unrealizedPct']:>+8.2f}%")

    if rep["realizedBySymbol"]:
        print("\n已实现盈亏（已清仓部分）")
        for s, v in rep["realizedBySymbol"].items():
            print(f"  {s:<7}{v:>+10.2f}")
    print(f"\n持仓市值 {t['marketValue']:.2f}  已实现 {t['realizedPnl']:+.2f}  "
          f"手续费 {t['fees']:.2f}  →  净盈亏 {t['netPnl']:+.2f} USDC")
    print("注: 不含股票账户的 USDC 现金余额，币安未开放该接口。")


# ---------------------------------------------------------------- KDJ
def fetch_klines(cli_fapi, symbol, interval="1d", limit=1000):
    return cli_fapi.request("GET", "/fapi/v1/klines",
                            {"symbol": symbol, "interval": interval, "limit": limit},
                            signed=False)


def _utc_date(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc)


def _cache_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "binance-stocks", "no_trading.json")


def _load_cache():
    try:
        with open(_cache_path()) as f:
            return [{"startTime": s, "endTime": e, "type": "NO_TRADING"}
                    for s, e in json.load(f)]
    except Exception:
        return []


def _save_cache(sessions):
    try:
        p = _cache_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            json.dump(sorted({(s["startTime"], s["endTime"])
                              for s in sessions if s["type"] == "NO_TRADING"}), f)
    except Exception:
        pass          # 缓存写不进去不影响主流程, 只是这次少累积一点


def fetch_trading_schedule(cli_fapi):
    """取美股交易时段表, 并与本地缓存合并。

    币安把美股时段切成四段且首尾相接, 正好铺满一个 UTC 日:
        OVERNIGHT    00:00~08:00 UTC   (美东前一日 20:00~04:00)
        PRE_MARKET   08:00~13:30 UTC
        REGULAR      13:30~20:00 UTC
        AFTER_MARKET 20:00~24:00 UTC
    所以一根 UTC 日 K = 一个完整的美股交易日（含盘前盘后夜盘），
    停市时段（周末、美股假期）标记为 NO_TRADING。

    **必须配缓存。** 接口只覆盖查询日前后各约一周, 窗口随时间向前滑动 ——
    历史停市日滑出窗口后会退回按星期判断, 于是同一个历史日期的 KDJ 会随查询
    时间变化（实测 2026-09-07 劳动节滑出后被误判为交易日, 使 K 偏移 0.7）。
    把见过的停市日累积下来, 结果才稳定。
    """
    d = cli_fapi.request("GET", "/fapi/v1/tradingSchedule", {}, signed=False)
    live = d.get("marketSchedules", {}).get("EQUITY", {}).get("sessions", [])
    merged = live + _load_cache()
    _save_cache(merged)
    return merged


def closed_days(schedule, bars):
    """返回「整日停市」的 K 线索引集合。

    判定: 该 UTC 日与所有 NO_TRADING 区间的交集时长 == 整日时长。
    这比硬编码"周一到周五"严谨 —— 美股假期会被时段表自动标成 NO_TRADING,
    硬编码则会把假期当成交易日混进来。

    落在时段表覆盖范围之外的 K 线, 退回按 UTC 星期判断(周一~周五为交易日)。
    """
    if not schedule:
        return {i for i, b in enumerate(bars) if _utc_date(b[0]).weekday() >= 5}
    lo = min(s["startTime"] for s in schedule)
    hi = max(s["endTime"] for s in schedule)
    day_ms = 86400000
    closed = set()
    for i, b in enumerate(bars):
        d0 = (b[0] // day_ms) * day_ms
        d1 = d0 + day_ms
        if d0 < lo or d1 > hi:                      # 时段表没覆盖, 退回星期判断
            if _utc_date(b[0]).weekday() >= 5:
                closed.add(i)
            continue
        covered = sum(max(0, min(s["endTime"], d1) - max(s["startTime"], d0))
                      for s in schedule if s["type"] == "NO_TRADING")
        if covered >= day_ms:
            closed.add(i)
    return closed


def compute_kdj(bars, n=9, m1=3, m2=3):
    """KDJ(N, M1, M2)。K/D 初值取 50，与通达信等主流软件一致。

    前 N-1 根用不足 N 的窗口（与 LLV/HHV 的行为一致），所以开头几根仅供预热，
    真正可用要等 K/D 收敛。平滑系数 1/3，实测 20 根后初值残留仅 1.5e-2。
    """
    out = []
    k = d = 50.0
    for i in range(len(bars)):
        win = bars[max(0, i - n + 1): i + 1]
        hi = max(float(b[2]) for b in win)
        lo = min(float(b[3]) for b in win)
        close = float(bars[i][4])
        rsv = 50.0 if hi == lo else (close - lo) / (hi - lo) * 100
        k = ((m1 - 1) * k + rsv) / m1
        d = ((m2 - 1) * d + k) / m2
        out.append((k, d, 3 * k - 2 * d))
    return out


def trading_bars(perp):
    """取某合约的日K并剔除停市日, 返回 (交易日 bars, 剔除数, 全部 bars)。

    KDJ 与 RSI 共用此口径。永续 24 小时在跑，停市日（周末 + 美股假期）照出 K 线，
    但不对应正股行情，会在指标窗口里占位。停市日判定与缓存逻辑见 fetch_trading_schedule。

    第三个返回值是**未过滤**的原始 bars，只有 --compare 核对口径时才用得上。
    它是被 RSI 重构漏掉过一次的东西：当时 trading_bars 只返回过滤后的结果，
    而 kdj_for 的 compare 分支还在引用原来的 bars，导致 `kdj --compare`
    一直抛 NameError。所以这里显式把它带出来，别再让调用方自己拼。
    """
    cli = Client(base=FAPI_BASE)
    bars = fetch_klines(cli, perp)
    if not bars:
        return None, 0, None
    closed = closed_days(fetch_trading_schedule(cli), bars)
    trading = [b for i, b in enumerate(bars) if i not in closed]
    return (trading or None), len(closed), bars


def kdj_for(perp, compare=False):
    """计算某合约的日K KDJ，口径为「仅交易日」。

    返回 (info, bars, kdj)；无数据时返回 (None, None, None)。
    """
    trading, nclosed, allbars = trading_bars(perp)
    if not trading:
        return None, None, None

    kdj = compute_kdj(trading)
    k, d, j = kdj[-1]
    last = trading[-1]
    info = {
        "perp": perp,
        "K": round(k, 2), "D": round(d, 2), "J": round(j, 2),
        "signal": _signal(k, d, j),
        "basis": "tradingDays",
        "bars": len(trading),
        "closedDaysExcluded": nclosed,
        "range": f"{_utc_date(trading[0][0]):%Y-%m-%d} ~ {_utc_date(last[0]):%Y-%m-%d}",
        "lastBarDate": f"{_utc_date(last[0]):%Y-%m-%d}",
        # 最后一根尚未收盘时 KDJ 仍会变，调用方需要知道这个值是不是终值
        "lastBarClosed": time.time() * 1000 >= last[6],
    }
    if compare:   # 仅供核对口径，默认不输出
        ka = compute_kdj(allbars)[-1]
        info["compareAllBars"] = {"K": round(ka[0], 2), "D": round(ka[1], 2),
                                  "J": round(ka[2], 2), "bars": len(allbars)}
    return info, trading, kdj


def _signal(k, d, j):
    """给一句人话解读。阈值用常见的 80/20，不做投资建议。"""
    tags = []
    if k > 80 and d > 80:
        tags.append("超买区")
    elif k < 20 and d < 20:
        tags.append("超卖区")
    if j > 100:
        tags.append("J 高位")
    elif j < 0:
        tags.append("J 低位")
    return "、".join(tags) if tags else "中性区"


def _print_kdj_table(info, bars, kdj, tail=8):
    # RSV 需要 n=9 根才有完整窗口, K/D 平滑系数 1/3 —— 实测 20 根后初值残留 <2e-2,
    # 所以 20 根以上即可用; 低于此值 K/D 仍在被初值 50 牵引。
    warn = "  ⚠ 样本偏少, K/D 仍在收敛中" if len(bars) < 20 else ""
    print(f"  {'日期':<12}{'收盘':>9}{'K':>8}{'D':>8}{'J':>8}{warn}")
    for b, (k, d, j) in list(zip(bars, kdj))[-tail:]:
        tag = "  ← 未收盘" if not info["lastBarClosed"] and b is bars[-1] else ""
        print(f"  {_utc_date(b[0]):%Y-%m-%d %a}{float(b[4]):>9.2f}"
              f"{k:>8.2f}{d:>8.2f}{j:>8.2f}{tag}")
    print(f"  当前: K={info['K']:.2f}  D={info['D']:.2f}  J={info['J']:.2f}   [{info['signal']}]")


def cmd_kdj(args):
    cli = Client()
    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        symbols = sorted(fetch_holdings(cli))
        if not symbols:
            print("当前没有股票代币持仓。")
            return

    result = {}
    for sym in symbols:
        perp = f"{sym}USDT"
        try:
            info, bars, kdj = kdj_for(perp, compare=args.compare)
        except BinanceError as e:
            print(f"\n{sym}: 取 {perp} K线失败 — {e}")
            continue
        if not info:
            print(f"\n{sym}: {perp} 无K线数据")
            continue

        result[sym] = info
        if not args.json:
            print("═" * 74)
            print(f"{sym}   ({perp})   日K KDJ(9,3,3)   口径: 仅交易日")
            print(f"{info['range']}   共 {info['bars']} 个交易日 "
                  f"(已剔除 {info['closedDaysExcluded']} 个停市日: 周末及美股假期)")
            print("═" * 74)
            _print_kdj_table(info, bars, kdj)
            if not info["lastBarClosed"]:
                print(f"  注: {info['lastBarDate']} 这根尚未收盘，K/D/J 到 UTC 日结束前仍会变。")
            if args.compare and "compareAllBars" in info:
                c = info["compareAllBars"]
                print(f"  [核对] 若含停市日({c['bars']}根): "
                      f"K={c['K']:.2f} D={c['D']:.2f} J={c['J']:.2f}")

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- RSI
def compute_rsi(bars, period=14):
    """Wilder RSI。返回与 bars 等长的列表，预热期为 None。

    前 period 个涨跌幅用简单平均做种子，其后按 Wilder 平滑：
        avg = (avg * (period - 1) + x) / period
    种子残留为 ((period-1)/period)^k —— 衰减很慢，period=24 要约 70 根才降到 5% 以下。
    所以本函数不做收敛判断，由 _rsi_reliable 单独报告，避免给出看着像模像样的错数。
    """
    closes = [float(b[4]) for b in bars]
    n = len(closes)
    out = [None] * n
    if n <= period:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, n)]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, n)]

    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    out[period] = _rsi_value(ag, al)
    for i in range(period, n - 1):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        out[i + 1] = _rsi_value(ag, al)
    return out


def _rsi_value(avg_gain, avg_loss):
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0    # 全涨=100; 全平=50(约定)
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def _rsi_seed_influence(period, n_bars):
    """当前值有多大比例仍由初始 SMA 种子决定, 范围 0~1。

    Wilder 平滑等价于 alpha=1/N 的 EMA, 种子权重为 ((N-1)/N)^k。
    这是连续量而非是非题 —— 与其打一个「可用/不可用」的标签, 不如把比例报出来,
    让调用方自己判断能信几分。
    """
    steps = n_bars - period - 1
    if steps < 0:
        return None
    if period <= 1:
        return 0.0
    return ((period - 1) / period) ** steps


def _rsi_reliable(period, n_bars):
    """种子影响是否已降到 5% 以下。"""
    inf = _rsi_seed_influence(period, n_bars)
    return inf is not None and inf <= 0.05


def _rsi_min_bars(period):
    """达到 5% 种子残留所需的最少 K 线数（解析解，非估算）。"""
    if period <= 1:
        return period + 1
    return period + 1 + math.ceil(math.log(0.05) / math.log((period - 1) / period))


def _parse_periods(text):
    try:
        ps = [int(x) for x in str(text).replace(" ", "").split(",") if x]
    except ValueError:
        raise SystemExit(f"--periods 格式错误: {text}（应形如 6,12,24）")
    if not ps or any(p < 1 for p in ps):
        raise SystemExit(f"--periods 必须为正整数: {text}")
    return sorted(set(ps))


def rsi_for(perp, periods):
    """计算某合约的日K RSI，口径为「仅交易日」。返回 (info, bars, series)。"""
    trading, nclosed, _ = trading_bars(perp)
    if not trading:
        return None, None, None
    series = {p: compute_rsi(trading, p) for p in periods}
    last = trading[-1]
    n = len(trading)
    info = {
        "perp": perp,
        "basis": "tradingDays",
        "bars": n,
        "closedDaysExcluded": nclosed,
        "range": f"{_utc_date(trading[0][0]):%Y-%m-%d} ~ {_utc_date(last[0]):%Y-%m-%d}",
        "lastBarDate": f"{_utc_date(last[0]):%Y-%m-%d}",
        "lastBarClosed": time.time() * 1000 >= last[6],
        "rsi": {str(p): {
            "value": round(series[p][-1], 2) if series[p][-1] is not None else None,
            "seedInfluence": (round(_rsi_seed_influence(p, n), 3)
                              if _rsi_seed_influence(p, n) is not None else None),
            "reliable": _rsi_reliable(p, n),
        } for p in periods},
    }
    return info, trading, series


def _print_rsi_table(info, bars, series, periods, tail=8):
    ps = periods
    head = f"  {'日期':<12}{'收盘':>9}" + "".join(f"{'RSI'+str(p):>10}" for p in ps)
    print(head)
    for b, idx in zip(bars[-tail:], range(len(bars) - len(bars[-tail:]), len(bars))):
        row = f"  {_utc_date(b[0]):%Y-%m-%d %a}{float(b[4]):>9.2f}"
        for p in ps:
            v = series[p][idx]
            row += f"{v:>10.2f}" if v is not None else f"{'-':>10}"
        print(row + ("  ← 未收盘" if not info["lastBarClosed"] and b is bars[-1] else ""))
    cur = "  " + f"{'当前':<12}{'':>9}" + "".join(
        f"{series[p][-1]:>10.2f}" if series[p][-1] is not None else f"{'-':>10}" for p in ps)
    print(cur)
    cells = []
    for p in ps:
        d = info["rsi"][str(p)]
        si = d["seedInfluence"]
        if si is None:
            cells.append("样本不足")
        elif d["reliable"]:
            cells.append("✅")
        else:
            cells.append(f"⚠ 种子{si*100:.0f}%")
    print("  " + f"{'种子占比':<12}{'':>9}" + "".join(f"{c:>10}" for c in cells))


def cmd_rsi(args):
    periods = _parse_periods(args.periods)
    cli = Client()
    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        symbols = sorted(fetch_holdings(cli))
        if not symbols:
            print("当前没有股票代币持仓。")
            return

    result = {}
    for sym in symbols:
        perp = f"{sym}USDT"
        try:
            info, bars, series = rsi_for(perp, periods)
        except BinanceError as e:
            print(f"\n{sym}: 取 {perp} K线失败 — {e}")
            continue
        if not info:
            print(f"\n{sym}: {perp} 无K线数据")
            continue

        result[sym] = info
        if not args.json:
            print("═" * (24 + 10 * len(periods)))
            print(f"{sym}   ({perp})   日K RSI{tuple(periods)}   口径: 仅交易日")
            print(f"{info['range']}   共 {info['bars']} 个交易日 "
                  f"(已剔除 {info['closedDaysExcluded']} 个停市日: 周末及美股假期)")
            print("═" * (24 + 10 * len(periods)))
            _print_rsi_table(info, bars, series, periods)
            bad = [p for p in periods if not info["rsi"][str(p)]["reliable"]]
            if bad:
                need = "、".join(f"RSI{p} 需 {_rsi_min_bars(p)} 根" for p in bad)
                print(f"  ⚠ RSI{','.join(map(str, bad))} 的种子占比仍高于 5%"
                      f"（{need}，当前 {info['bars']} 个交易日）—— 数值是标准定义，"
                      f"但偏离程度受初始种子牵引，慎用。")
            if not info["lastBarClosed"]:
                print(f"  注: {info['lastBarDate']} 这根尚未收盘，RSI 到 UTC 日结束前仍会变。")

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- 交易
# 这一节会花真钱。设计目标不是"把下单接口包一层", 而是让调用方(尤其是 agent)
# 无法在用户没看过预览的情况下把单子发出去, 以及在网络超时时**不谎报**结果。
MIN_NOTIONAL = 5.0            # 实测二分: 0.01507749 股被拒 / 0.01507750 股放行 (AAPL $331.45)
MAX_NOTIONAL = 1_000_000.0    # 实测二分
DEFAULT_MAX_ORDER = 500.0     # 单笔护栏, --max 覆盖
FLAT_FEE = 0.17               # 实测: **固定费, 与金额无关**。
                              # 实测多笔历史成交, 成交额跨两个数量级, 手续费全都是 0.17。
                              # 别改成分比例 —— 那会让小额单差一个数量级
                              # ($10 的单会估成 0.018, 实际 0.17)。
CONFIRM_TTL_MS = 10 * 60 * 1000


def _pending_path():
    return os.path.join(os.path.dirname(_cache_path()), "pending.json")


def _load_pending():
    try:
        with open(_pending_path()) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_pending(d):
    p = _pending_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)


def issue_confirm_code(params):
    """生成一次性确认码, 连同下单参数存到本地。

    刻意用**随机码**而不是"参数哈希": 哈希能从公开源码复算出来, agent 就能绕过
    预览直接下单; 随机码存在本地状态文件里, 不复现预览这一步根本拿不到。
    """
    code = secrets.token_hex(2)                     # 4 位 hex
    now = int(time.time() * 1000)
    pend = {c: v for c, v in _load_pending().items() if v.get("expiresAt", 0) > now}
    pend[code] = dict(params, createdAt=now, expiresAt=now + CONFIRM_TTL_MS)
    _save_pending(pend)
    return code


def consume_confirm_code(code, params):
    """校验并**消费**确认码。参数必须逐字段一致, 且未过期。"""
    now = int(time.time() * 1000)
    pend = _load_pending()
    ent = pend.get(code)
    if not ent:
        raise ValueError("确认码无效（不存在、已用过，或已被覆盖）。请重新运行预览取新码。")
    if ent.get("expiresAt", 0) <= now:
        pend.pop(code, None)
        _save_pending(pend)
        raise ValueError("确认码已过期（有效期 10 分钟）。请重新运行预览取新码。")
    for k, v in params.items():
        if ent.get(k) != v:
            raise ValueError(
                f"确认码与当前参数不符（{k}: 预览时是 {ent.get(k)!r}, 现在是 {v!r}）。"
                f"确认码只对生成它的那一次预览有效, 请重新预览。")
    pend.pop(code, None)                            # 一次性: 用过即废
    _save_pending(pend)


def fetch_order_detail(cli, order_id=None, client_order_id=None):
    p = {}
    if order_id:
        p["orderId"] = order_id
    if client_order_id:
        p["clientOrderId"] = client_order_id
    return cli.request("GET", "/sapi/v1/equity/order/detail", p)


def _funding_usdc(cli, rows=None):
    """资金账户 USDC 余额 —— **只是提示, 不是权威判断**。

    股票账户是否有独立的现金余额查不到 (equity/balance 等 26 条路径全 404),
    所以这里返回 None 表示"问不到", 0.0 表示"确实没有"。
    能不能成交最终交给币安判, 错误原样透传。

    rows 可由调用方传进来复用 —— 实测这个接口单次 ~1.2 秒, 同一轮里拉两次
    纯属白等 (买入路径尤其等不起)。
    """
    if rows is None:
        try:
            rows = cli.request("POST", "/sapi/v1/asset/get-funding-asset")
        except BinanceError:
            return None
    if not rows:
        return None
    for r in rows:
        if r.get("asset") == "USDC":
            return float(r.get("free", 0) or 0)
    return 0.0


def _usable_quote(cli, symbol):
    """取盘口, 并确认它**可用**。

    实测币安对不存在的标的给出的东西不统一: "ZZZZ" 是 HTTP 200 + 空响应体
    (client 已转成 BinanceError), 而 "NOPE" 却是一份全 0 的盘口
    {"bidPrice":"0","askPrice":"0"}。后者直接拿去做除法就是 ZeroDivisionError,
    对调用方毫无信息量。真实的"当前无报价"(停牌等)也是这个形态, 所以统一在这里挡掉。
    """
    q = fetch_quote(cli, symbol)
    try:
        bid = float(q.get("bidPrice") or 0)
        ask = float(q.get("askPrice") or 0)
    except (TypeError, ValueError):
        bid = ask = 0.0
    if bid <= 0 or ask <= 0:
        # 这是本地校验, 不是币安报错, 所以用 ValueError(走干净的错误输出),
        # 免得打成"币安接口报错: HTTP 200"这种自相矛盾的提示。
        raise ValueError(
            f"{symbol} 当前没有有效盘口（bid={q.get('bidPrice')!r}, "
            f"ask={q.get('askPrice')!r}）。可能是代码写错、标的不存在, "
            f"或该标的当前无报价。")
    return q


def _holding_state(cli, ticker):
    """返回 (可卖数量原始字符串, 被锁数量, 承载资产名, 其他形态备注列表)。

    卖出必须用**原值字符串**, 不能让 float 往返 —— 尾数多出一点就会被判超卖。
    另外只有 free 能卖: 实测见过某标的约 10% 的数量被挂着的卖单锁住,
    那部分 --all 卖不掉也不该卖。

    不依赖 `EQ_` 前缀: BABA 那笔持仓在 `BABAB`(bStock) 里, 只认 EQ_ 会让本地
    预检判成"未持有"而把单子挡在本地 —— 根本到不了币安, 用户却以为是自己没仓位。
    同一标的若两种形态都有余额, 取余额大的那个承载卖出, 其余的在备注里点明:
    合并成一个数会让 --all 悄悄只卖掉一部分。
    """
    rows = cli.request("POST", "/sapi/v1/asset/get-funding-asset")
    bs = fetch_bstocks(cli)
    if bs is None:
        print("  ⚠ 拿不到 bStocks 名单，非 EQ_ 形态的持仓可能认不出来。", file=sys.stderr)
    cands = []
    for r in rows:
        if row_ticker(r, bs) != ticker:
            continue
        free_s = str(r.get("free", "0") or "0")
        if float(free_s) > 0:
            cands.append((float(free_s), free_s, str(r.get("asset", "")),
                          float(r.get("locked", 0) or 0)))
    if not cands:
        return ("0", 0.0, None, [])
    cands.sort(key=lambda c: -c[0])
    _, free_s, asset, locked = cands[0]
    others = [f"{a} {f:g} 股" for f, _, a, _ in cands[1:]]
    return (free_s, locked, asset, others)


def place_market_order(cli, symbol, side, notional=None, quantity=None):
    """下市价单, 返回订单详情。

    超时处理是这里的重点: 本机到币安实测会卡(有过 >3 分钟), 而币安默认
    recvWindow 只有 5s。网络异常时我们**不知道单子到底成没成** —— 所以用
    clientOrderId 反查真实结果, 而不是猜一个"失败"出来。
    """
    cid = f"stk{int(time.time())}{secrets.token_hex(4)}"
    p = {"symbol": symbol, "side": side, "orderType": "MARKET",
         "newClientOrderId": cid}
    if notional is not None:
        p["notional"] = notional
    else:
        p["quantity"] = quantity
    placed_after_ms = int(time.time() * 1000)
    try:
        resp = cli.request("POST", "/sapi/v1/equity/order/place", p, timeout=60)
    except BinanceError:
        raise                       # 币安明确拒单 = 没下出去, 如实上报
    except Exception:
        return _recover(cli, symbol, side, cid, placed_after_ms)

    # 实测(一笔卖单): place 的响应体是 {"status": "S", "orderId": ...}
    # 这种骨架 —— 有 status, 但**一个成交字段都没有**, 而 "S" 也不是
    # FILLED/NEW 那套状态码。之前只要看见 status 就直接返回它, 于是单子明明
    # 已经 FILLED, 输出却是「状态 S / 数量 None / 均价 -」外加一个 ⏳, 看着像
    # 没成交 —— 而"像没成交"会直接诱导用户重复下单。
    # 所以判断依据不能是 status 存不存在, 而是**有没有真的带回成交信息**。
    if isinstance(resp, dict) and _has_fill_info(resp):
        return resp
    # 补查详情**必须优先用 orderId**。实测: 币安根本不采纳
    # 我们传的 newClientOrderId —— place 回显的 clientOrderId 是它自己生成的
    # "def_...", 拿我们那个 "stk..." 去查直接 `Order not found`。骨架响应里
    # 恰好带 orderId, 而 orderId 查得到, 所以按 orderId 走。
    oid = resp.get("orderId") if isinstance(resp, dict) else None
    try:
        if oid:
            return fetch_order_detail(cli, order_id=oid)
        return fetch_order_detail(cli, client_order_id=cid)
    except BinanceError:
        return resp if isinstance(resp, dict) else {"raw": resp}


def _has_fill_info(d):
    """响应里是否带回了可用的成交信息。

    必须有 filledQty / avgFilledPrice / filledTotal 之一且非空非零 ——
    挂单未成交时这几个字段是 "0"/空, 那种情况同样要补查详情拿状态。
    """
    return any(str(d.get(k) or "").strip() not in ("", "0", "0.0")
               for k in ("filledQty", "avgFilledPrice", "filledTotal"))


_CANT_TELL = ("**先去币安 App 核对持仓与委托, 确认无误再决定要不要重下 —— "
              "不要直接重复下单。**")


def _recover(cli, symbol, side, cid, placed_after_ms):
    """下单请求异常后的对账: 这笔单子到底下出去没有。

    **不能只按 clientOrderId 反查。** 实测: 币安不采纳我们传的
    newClientOrderId, 它回的 clientOrderId 是自己生成的 "def_...", 拿我们的
    "stk..." 去查一律 `Order not found`。所以"按 cid 查不到"**根本不代表没下出去**,
    照旧逻辑会报"大概率没有成交", 直接诱导用户重复下单 —— 这是最危险的一种错。

    改为两级: 先按 cid 试(万一哪天认可了), 查不到再退回**按最近成交匹配**
    (symbol + side + 时间窗)。匹配不唯一时如实说"分不清", 绝不猜。
    """
    try:
        return fetch_order_detail(cli, client_order_id=cid)
    except BinanceError:
        pass
    try:
        rows = fetch_orders(cli, days=1)
    except Exception as e:                       # 连流水都查不了
        raise BinanceError(0, {
            "msg": f"下单请求没有收到响应, 且反查流水也失败({e}). {_CANT_TELL}",
            "clientOrderId": cid}) from None
    cutoff = placed_after_ms - 15000             # 留余量, 防本机与服务器时钟差
    hits = [r for r in rows
            if r.get("symbol") == symbol and r.get("side") == side
            and int(r.get("createdAt") or 0) >= cutoff]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise BinanceError(0, {
            "msg": f"下单请求没有收到响应, 最近成交里也没有 {symbol} {side} 的记录。"
                   f"**大概率没有成交**, 但{_CANT_TELL}",
            "clientOrderId": cid}) from None
    raise BinanceError(0, {
        "msg": f"下单请求没有收到响应, 而最近一分钟内有 {len(hits)} 笔 "
               f"{symbol} {side} 的成交, 无法确定哪一笔是这次的。{_CANT_TELL}",
        "clientOrderId": cid, "candidates": [h.get("orderId") for h in hits]}) from None


def _check_notional(usd, cap):
    if usd < MIN_NOTIONAL:
        raise ValueError(f"金额 ${usd:.2f} 低于币安最小下单额 ${MIN_NOTIONAL:.2f}。")
    if usd > MAX_NOTIONAL:
        raise ValueError(f"金额 ${usd:.2f} 超过币安上限 ${MAX_NOTIONAL:,.0f}。")
    if usd > cap:
        raise ValueError(
            f"金额 ${usd:.2f} 超过单笔护栏 ${cap:,.2f}。确认要下这么大的单, "
            f"请显式加 --max {usd:.0f}。")


def _fee_line(cost):
    pct = FLAT_FEE / cost * 100 if cost else 0
    s = f"手续费 ${FLAT_FEE:.2f}（固定, 占 {pct:.2f}%）"
    if pct >= 1.0:
        s += f"\n  ⚠ 固定手续费摊在这笔小额单上占到 {pct:.1f}%，成本很高"
    return s


def _print_preview(human, q, est_qty, cost, code, argv, extra=None):
    print("─" * 74)
    print("  预览（未下单）")
    print(f"  {human}")
    print(f"  盘口 {q['bidPrice']} / {q['askPrice']}      预估 {est_qty:.8f} 股")
    print(f"  {_fee_line(cost)}")
    if extra:
        print(f"  {extra}")
    print("─" * 74)
    print(f"  确认码: {code}   （10 分钟内有效, 只能用一次）")
    print("\n  执行下单:")
    print(f"    {' '.join(argv)} --confirm {code}")


def _render_fill(d, symbol, side):
    status = d.get("status", "?")
    known = _has_fill_info(d)
    print("─" * 74)
    if status == "FILLED":
        mark = "✅"
    elif known:
        mark = "⏳"
    else:
        # 既不是 FILLED, 又没有任何成交字段 —— 这时**我们不知道**成没成。
        # 不能画个 ⏳ 就当"还在挂着", 那会让人以为可以安心等或者重下。
        mark = "❓"
    print(f"  {mark} {symbol}  {side}  状态 {status}")
    print(f"  数量 {d.get('filledQty') or '-'} 股   "
          f"成交均价 {d.get('avgFilledPrice') or '-'}   "
          f"金额 {d.get('filledTotal') or '-'} USDC   "
          f"手续费 {d.get('fee') or '-'}")
    if d.get("orderId"):
        print(f"  订单号 {d['orderId']}")
    if not known and status != "FILLED":
        print("  ⚠ 没拿到成交明细, 这笔单子的真实状态未知。"
              "请先到币安 App 核对持仓与委托, **不要直接重复下单**。")
    print("─" * 74)


def assess_buy_form(cli, symbol, rows=None):
    """买入前判断这笔会落到哪种资产形态。返回 (ok, 文案)。

    为什么要有这道闸：同一条股票接口，买 MUU 落到 `EQ_MUU`，买 BABA 却落到
    `BABAB`(bStock)。**接口层面推不出会落哪种**，所以这里不猜，
    只检查能确证的两件事：
      - 账户里是否已有 `EQ_<代码>` —— 该标的的 EQ_ 形态对你的账户是通的
      - `<代码>B` 是否在 bStocks 名单里 —— 该标的存在 bStock 形态
    确认不了就拦下，要用户显式放行才继续。
    """
    try:
        bs = fetch_bstocks(cli)
        if rows is None:
            rows = cli.request("POST", "/sapi/v1/asset/get-funding-asset")
    except Exception:
        return (False, f"买入前无法核对 {symbol} 的到账形态（查询失败）。"
                       f"为避免买错先拦下，稍后重试；确认可以接受任意形态可加 --allow-bstock。")
    eq_asset = f"EQ_{symbol}"
    if any(str(r.get("asset")) == eq_asset for r in rows):
        return (True, None)
    bst = f"{symbol}B"
    if bst in bs:
        return (False,
                f"{symbol} 的账户记录里没有 {eq_asset}，但它有 bStock 形态 {bst}。\n"
                f"  实测踩过这个坑：此时下单到账的多半是 bStock，而不是 {eq_asset}。\n"
                f"  确认你要的就是 bStock，加 --allow-bstock 再下。")
    return (False,
            f"无法判断 {symbol} 会以哪种形态到账：账户里没有 {eq_asset}，"
            f"bStocks 名单里也没有 {bst}。\n"
            f"  为避免买错，默认拦下。确认可以接受任意形态再加 --allow-bstock。")


def verify_fill_form(cli, symbol):
    """成交后核对**实际到账的形态**。返回提示文案，正常（EQ_）返回 None。

    这一步不能省：买完不看，就可能像那次 BABA 一样 —— 资产落到 bStock，
    而用户以为拿到的是 EQ_*，直到想卖的时候才发现卖不动。
    """
    try:
        rows = cli.request("POST", "/sapi/v1/asset/get-funding-asset")
        bs = fetch_bstocks(cli)
    except Exception:
        return (f"⚠ 没能核对到账形态（查询失败）。请自行到币安 App 确认拿到的是 "
                f"EQ_{symbol} 还是 {symbol}B。")
    hits = [(str(r.get("asset")), float(r.get("free", 0) or 0))
            for r in rows if row_ticker(r, bs) == symbol and float(r.get("free", 0) or 0) > 0]
    if not hits:
        return None
    eq = [f"{a} {f:g} 股" for a, f in hits if a.startswith("EQ_")]
    other = [f"{a} {f:g} 股" for a, f in hits if not a.startswith("EQ_")]
    if other and not eq:
        return (f"⚠ **到账的是 bStock 形态，不是 EQ_{symbol}**：{'、'.join(other)}。\n"
                f"  它不在 EQ_ 体系里。持仓报表已能识别它，也可以正常卖出。")
    if other and eq:
        return (f"⚠ {symbol} 同时存在两种形态：{'、'.join(eq)}；{'、'.join(other)}。\n"
                f"  卖出的 --all 只会卖掉其中余额较大的一份。")
    return None


def _emit(args, preview_obj, fill_obj, human, q, est_qty, cost, argv, extra=None):
    """预览 / 成交两条路径的公共出口。"""
    if args.confirm is None:
        code = issue_confirm_code(fill_obj["_params"])
        fill_obj.pop("_params")
        preview_obj["confirmCode"] = code
        if args.json:
            print(json.dumps(preview_obj, indent=2, ensure_ascii=False))
        else:
            _print_preview(human, q, est_qty, cost, code, argv, extra)
        return

    consume_confirm_code(args.confirm, fill_obj["_params"])
    fill_obj.pop("_params")
    detail = place_market_order(_client_for_trade(), fill_obj["symbol"],
                               fill_obj["side"],
                               notional=fill_obj.get("notional"),
                               quantity=fill_obj.get("quantity"))
    out = {"preview": False,
           "orderId": detail.get("orderId"),
           "clientOrderId": detail.get("clientOrderId"),
           "symbol": detail.get("symbol", fill_obj["symbol"]),
           "side": detail.get("side", fill_obj["side"]),
           "orderType": detail.get("orderType", "MARKET"),
           "status": detail.get("status"),
           "filledQty": detail.get("filledQty"),
           "avgFilledPrice": detail.get("avgFilledPrice"),
           "filledTotal": detail.get("filledTotal"),
           "fee": detail.get("fee")}
    # 买入后强制核对到账形态 —— 这是买 BABA 那次教训的直接补救:
    # 买完不核对, 就会以为拿到 EQ_*, 到要卖的时候才发现是 bStock。
    if out["side"] == "BUY":
        note = verify_fill_form(_client_for_trade(), out["symbol"])
        if note:
            out["assetFormWarning"] = note
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        _render_fill(out, out["symbol"], out["side"])
        if out.get("assetFormWarning"):
            print("─" * 74)
            print("  " + out["assetFormWarning"].replace("\n", "\n  "))
            print("─" * 74)


_TRADE_CLI = None


def _client_for_trade():
    global _TRADE_CLI
    if _TRADE_CLI is None:
        _TRADE_CLI = Client()
    return _TRADE_CLI


def cmd_buy(args):
    if not args.symbol or args.usd is None:
        sys.exit("用法: stocks.py buy <代码> --usd <金额>\n"
                 "  不带 --confirm 只预览; 带上预览给出的确认码才真正下单。")
    symbol = args.symbol.upper()
    try:
        usd = float(args.usd)
    except ValueError:
        sys.exit(f"--usd 不是合法数字: {args.usd!r}")
    try:
        _check_notional(usd, args.max)
    except ValueError as e:
        sys.exit(str(e))

    cli = _client_for_trade()
    cost = round(usd, 2)
    notional_s = f"{cost:.2f}"
    params = {"symbol": symbol, "side": "BUY", "orderType": "MARKET",
              "notional": notional_s, "quantity": None}
    fill_obj = {"symbol": symbol, "side": "BUY", "notional": notional_s,
                "quantity": None, "_params": params}

    if args.confirm is not None:
        # 确认路径 = 真正下单的那一步, **必须最短**。
        # 形态检查、盘口检查在预览时都做过了, 而且确认码与当时的参数逐字段绑定,
        # 在这里重做既拿不到新信息, 又会让行情在等待中走掉。实测各接口单次
        # 1.2~13 秒, 下单前少发一次就少等 1~13 秒。所以这里**只发 place 一次**。
        _emit(args, None, fill_obj, None, None, None, None, None, None)
        return

    # ↓ 以下只在预览路径执行 —— 用户要读这些信息, 慢一点可以接受。
    try:
        rows = cli.request("POST", "/sapi/v1/asset/get-funding-asset")
    except Exception:
        rows = None

    # 第一道闸: 确认这笔会落到 EQ_* 还是 bStock。查不出就拦下 ——
    # 买错了在账面上当时看不出来, 等到要卖才发现。
    ok, why = assess_buy_form(cli, symbol, rows=rows)
    if not ok and not getattr(args, "allow_bstock", False):
        sys.exit("已拦下（避免买错资产形态）:\n  " + why.replace("\n", "\n  "))

    q = _usable_quote(cli, symbol)                  # 无有效盘口会在这里报错
    est_qty = cost / float(q["askPrice"])

    extra = None
    u = _funding_usdc(cli, rows=rows)               # 复用上面那次查询, 不重复拉
    if u == 0.0:
        extra = "⚠ 资金账户未发现 USDC；若股票账户也无独立余额, 此单会被拒（需先在币安端充值/划转）"

    _emit(args,
          {"preview": True, "symbol": symbol, "side": "BUY", "orderType": "MARKET",
           "notional": notional_s, "quote": q,
           "estQty": f"{est_qty:.8f}", "estFee": f"{FLAT_FEE:.2f}",
           "feePct": round(FLAT_FEE / cost * 100, 4)},
          fill_obj,
          f"买入 {symbol}   市价   ${cost:.2f} USDC", q, est_qty, cost,
          ["python3", "stocks.py", "buy", symbol, "--usd", f"{cost:g}"],
          extra)


def cmd_sell(args):
    if not args.symbol or (args.qty is None and not args.sell_all):
        sys.exit("用法: stocks.py sell <代码> --qty <股数>  |  stocks.py sell <代码> --all\n"
                 "  不带 --confirm 只预览; 带上预览给出的确认码才真正下单。")
    if args.qty is not None and args.sell_all:
        sys.exit("--qty 与 --all 不能同时用。")
    symbol = args.symbol.upper()
    cli = _client_for_trade()

    raw, locked, asset, others = _holding_state(cli, symbol)
    held = float(raw)
    if held <= 0:
        holds = fetch_holdings(cli)
        raise SystemExit(f"未持有 {symbol} 的可卖数量, 无法卖出（币安不支持卖空）。"
                         f"当前持有: {', '.join(sorted(holds)) or '无'}"
                         + (f"；{symbol} 另有 {locked:g} 股被挂单锁定" if locked else ""))

    if args.sell_all:
        qty_s = raw                                  # 原值字符串, 不做 float 往返
        qty = held
    else:
        try:
            qty = float(args.qty)
        except ValueError:
            sys.exit(f"--qty 不是合法数字: {args.qty!r}")
        if qty <= 0:
            sys.exit("--qty 必须大于 0。")
        if qty > held + 1e-12:
            sys.exit(f"卖出 {qty} 股超过持仓 {raw} 股。要全卖请用 --all。")
        qty_s = args.qty.strip()

    q = _usable_quote(cli, symbol)
    proceeds = qty * float(q["bidPrice"])
    try:
        _check_notional(proceeds, args.max)
    except ValueError as e:
        sys.exit(str(e) + "\n  （卖出的市值同样受单笔护栏约束; 平仓若超过请显式提高 --max）")

    params = {"symbol": symbol, "side": "SELL", "orderType": "MARKET",
              "notional": None, "quantity": qty_s}
    left = held - qty
    extra = f"卖出后剩余 {left:.8f} 股" if left > 1e-12 else "卖出后清仓"
    if asset:
        extra += f"\n  持仓在 {asset}" + ("（bStock 形态）" if not asset.startswith("EQ_") else "")
    if others:
        extra += f"\n  ⚠ 同一标的另有 {', '.join(others)}，--all 只卖 {asset} 这一份"
    if locked:
        extra += f"；另有 {locked:g} 股被挂单锁定, --all 只卖可用的部分"
    argv = ["python3", "stocks.py", "sell", symbol]
    argv += ["--all"] if args.sell_all else ["--qty", qty_s]

    _emit(args,
          {"preview": True, "symbol": symbol, "side": "SELL", "orderType": "MARKET",
           "quantity": qty_s, "quote": q,
           "estProceeds": f"{proceeds:.2f}", "estFee": f"{FLAT_FEE:.2f}",
           "feePct": round(FLAT_FEE / proceeds * 100, 4) if proceeds else None,
           "remainingAfter": f"{left:.8f}"},
          {"symbol": symbol, "side": "SELL", "notional": None,
           "quantity": qty_s, "_params": params},
          f"卖出 {symbol}   市价   {qty_s} 股（约 ${proceeds:.2f}）", q, qty,
          proceeds, argv, extra)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="币安股票代币持仓与指标查询")
    ap.add_argument("command", nargs="?", default="positions",
                    choices=["positions", "kdj", "rsi", "quote", "orders", "buy", "sell"])
    ap.add_argument("symbol", nargs="?", help="标的代码（kdj / quote 用）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--compare", action="store_true",
                    help="附带含停市日的口径供核对（默认不输出）")
    ap.add_argument("--periods", default="6,12,24",
                    help="RSI 周期，逗号分隔（默认 6,12,24）")
    ap.add_argument("--usd", help="买入金额（USDC），buy 用")
    ap.add_argument("--qty", help="卖出股数，sell 用")
    ap.add_argument("--all", dest="sell_all", action="store_true",
                    help="卖出全部持仓，sell 用")
    ap.add_argument("--allow-bstock", dest="allow_bstock", action="store_true",
                    help="买入时放行 bStock 形态（默认会拦下无法确认为 EQ_* 的标的）")
    ap.add_argument("--confirm", metavar="CODE",
                    help="确认码；不带则只预览，不下单")
    ap.add_argument("--max", type=float, default=DEFAULT_MAX_ORDER,
                    help=f"单笔金额上限（默认 {DEFAULT_MAX_ORDER:g}）")
    args = ap.parse_args()

    try:
        if args.command == "kdj":
            cmd_kdj(args)
            return
        if args.command == "rsi":
            cmd_rsi(args)
            return
        if args.command == "buy":
            cmd_buy(args)
            return
        if args.command == "sell":
            cmd_sell(args)
            return

        cli = Client()
        if args.command == "quote":
            if not args.symbol:
                sys.exit("用法: stocks.py quote <代码>")
            q = fetch_quote(cli, args.symbol.upper())
            print(json.dumps(q, indent=2, ensure_ascii=False) if args.json
                  else f"{q['symbol']}  买 {q['bidPrice']} / 卖 {q['askPrice']}")
            return

        if args.command == "orders":
            rows = fetch_orders(cli)
            if args.json:
                print(json.dumps(rows, indent=2, ensure_ascii=False))
                return
            print(f"{'时间':<17}{'代码':<7}{'方向':<6}{'类型':<8}{'数量':>14}{'成交价':>11}{'金额':>10}")
            for r in rows:
                ts = time.strftime("%m-%d %H:%M:%S", time.localtime(r["createdAt"] / 1000))
                px = r.get("avgFilledPrice") or r.get("limitPrice") or "-"
                print(f"{ts:<17}{r['symbol']:<7}{r['side']:<6}{r['orderType']:<8}"
                      f"{r['filledQty']:>14}{px:>11}{r['filledTotal']:>10}")
            return

        rep = build_report(cli)
        if args.json:
            print(json.dumps(rep, indent=2, ensure_ascii=False))
        else:
            render(rep)

    except BinanceError as e:
        sys.exit(f"币安接口报错: {e}")
    except ValueError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
