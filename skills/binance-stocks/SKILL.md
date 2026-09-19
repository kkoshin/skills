---
name: binance-stocks
description: Query AND trade Binance tokenized stocks (EQ_*): holdings, cost basis, live quotes, P&L, KDJ/RSI indicators, and placing market buy/sell orders. Use when the user asks about their Binance stock positions, tokenized equities, 币安股票持仓, 股票代币, EQ_ assets, or wants to 建仓/平仓 (open/close) a position such as TSLA or AAPL.
---

# 币安股票代币持仓与交易

查询币安股票代币（`EQ_*`，如 `EQ_KO`、`EQ_MUU`）的持仓、成本、行情与盈亏，
并可用市价单建仓 / 平仓。

> ⚠️ **`buy` / `sell` 是真实下单、真实资金。**
> 必须两步走：先跑预览拿确认码，**把预览给用户看过**之后，再拿确认码执行。
> 绝不要在用户没看过预览的情况下直接下单。

## 用法

脚本零依赖，只需标准库和环境变量：

```bash
python3 scripts/stocks.py            # 持仓 + 行情 + 盈亏（默认，人读）
python3 scripts/stocks.py --json     # 同上，机器可读
python3 scripts/stocks.py kdj        # 持仓标的的日K KDJ（仅交易日口径）
python3 scripts/stocks.py kdj KO     # 指定标的的 KDJ
python3 scripts/stocks.py rsi        # 持仓标的的日K RSI（默认 6,12,24）
python3 scripts/stocks.py rsi KO --periods 14
python3 scripts/stocks.py quote KO   # 单只实时买卖盘
python3 scripts/stocks.py orders     # 全部成交明细
```

交易（真实下单，两步确认）：

```bash
python3 scripts/stocks.py buy  TSLA --usd 100                 # ① 预览，打印确认码
python3 scripts/stocks.py buy  TSLA --usd 100 --confirm 7a3f  # ② 码对了才真下单
python3 scripts/stocks.py sell KO --qty 1                     # 部分平仓
python3 scripts/stocks.py sell KO --all                       # 全部平仓
```

环境变量（通常已由用户 shell 或 agent 运行环境提供）：

| 变量 | 说明 |
|---|---|
| `BINANCE_API_KEY` | API Key |
| `BINANCE_SECRET_KEY` | 密钥。**可以是密钥内容本身，也可以是指向 PEM 文件的路径** |
| `BINANCE_API_BASE` | 可选，默认 `https://api.binance.com` |

凭据类型（HMAC-SHA256 / Ed25519）自动识别，无需配置。

## 输出解读

默认输出三块：持仓表（数量 / 成本价 / 现价 / 市值 / 浮盈）、已实现盈亏（按标的）、汇总行。

- **成本价**用移动加权成本法从完整成交流水回放得出，不依赖接口的汇总字段。
- **现价**取实时 `bidPrice`（按能卖出的价格估值）；无 bid 时退回 `askPrice`。
- **净盈亏** = 浮动盈亏 + 已实现盈亏 − 手续费。
- 汇总**不含股票账户的 USDC 现金余额** —— 币安没有开放该接口，汇报时要说清楚这是纯持仓市值。

## 关键事实（改动前务必先读）

这些都是实测踩出来的，不是猜的：

1. **股票代币在【资金账户】里，不在现货账户。** 只查现货会得到「空账户」的错误结论。
2. **取持仓必须用 `/sapi/v1/asset/get-funding-asset`（POST）。** `/sapi/v3/asset/getUserAsset` 不返回 `EQ_*`。
3. **`BINANCE_SECRET_KEY` 两种约定都会遇到**：密钥内容，或指向 PEM 文件的路径（官方 `binance-cli` 用后者）。`Credentials` 两种都吃，改动时别破坏这个兼容。
4. **密钥常是 Ed25519（PKCS#8，base64），不是 HMAC。** 照抄普通教程的 HMAC-SHA256 会一直报 `-1022 Signature for this request is not valid`。Ed25519 的签名要 base64 编码，HMAC 才是 hex。
5. **`recvWindow` 必须放宽到 60000。** 币安默认 5000ms，而网络偶发卡顿（实测有卡死 >3 分钟的）会让请求到达时被判 `Timestamp for this request is outside of the recvWindow`，表现为随机失败。别改小。
6. **签名必须覆盖实际发出的那个字符串。** 先 `urlencode(sorted(...))` 编码一次并固定，再把 `signature` 追加到同一个串上。分别编码两次会在参数变多时签名不匹配。
7. **公开行情接口（`/api/v3/ticker/price` 等）不接受 `recvWindow`**，会报 `-1101 Too many parameters`。这类接口要 `signed=False` 调用。
8. **`binance-cli` 不支持股票代币。** 它的 30 个产品里没有 equity，二进制里搜 `sapi/v1/equity` / `stockTicker` / `EQ_` 全部 0 命中。别绕道去用它。

关于 KDJ：

9. **股票代币没有 K 线接口**，要从 **TradFi 永续**取：代币代码 → 合约代码就是 `{代码}USDT`，
   走标准 `/fapi/v1/klines`。合约需满足 `contractType=TRADIFI_PERPETUAL` 且
   `underlyingType=EQUITY`（183 个股票类）。
10. **KDJ 一律用「仅交易日」口径，脚本不输出含停市日的版本。** 永续 24 小时在跑，
    停市日（周末+美股假期）照样出 K 线，但量极低（MUUUSDT 周末量仅为工作日的 7.3%，
    KOUSDT 为 45.7%）。这些 bar 会在 RSV 窗口里占位却不对应任何正股行情，会把 K 推离
    真实水平（实测 MUU 的 K 差 10.9）。需要核对时可加 `--compare`，默认不输出，
    以免调用方误取。
11. **判定停市日必须用 `tradingSchedule`，不能硬编码「周一到周五」。** 美股四段
    （OVERNIGHT/PRE/REGULAR/AFTER）首尾相接正好铺满一个 UTC 日，所以一根 UTC 日 K
    就是一个完整美股交易日；停市区间标记为 `NO_TRADING`。实测 KOUSDT 在 8/6~9/15 区间，
    硬编码得 29 个交易日，时段表得 28 个 —— 差的是 9/7 劳动节。
12. **时段表只覆盖前后各约一周，必须配缓存。** 窗口随时间向前滑，历史停市日滑出后
    会退回按星期判断 —— 同一个历史日期的 KDJ 会随查询时间变化。所以脚本把见过的
    `NO_TRADING` 区间累积到 `~/.cache/binance-stocks/no_trading.json`（可用
    `XDG_CACHE_HOME` 改路径）。**已知边界**：早于首次运行、且已滑出窗口的历史假期
    补不回来，冷启动时会被计入，实测单个假期使 K 偏移约 0.7。此后每个假期都会在
    窗口内被捕获并永久缓存。删掉缓存会退回冷启动状态。
13. **RSI 的收敛比 KDJ 慢得多，必须看 `seedInfluence`。** Wilder 平滑的种子权重为
    `((N-1)/N)^k`：RSI6 需 24 根、RSI12 需 48 根、RSI24 需 **96** 根才降到 5%。
    币安永续历史普遍很短（KOUSDT 仅 41 根日 K），所以 RSI12/24 常常远未收敛 ——
    实测 KO 的 RSI24 种子占比 81%（数值基本由种子决定），MUU 的 RSI12 只有 6%。
    不要只看数值，`seedInfluence` 才决定能信几分。
14. **K 线历史很短**，等于合约上线时间（KOUSDT 仅 41 根，TSLLUSDT 仅 14 根）。
    少于 20 根时 K/D 仍在被初值 50 牵引，结论不可用。

关于交易：

15. **下单接口是 `/sapi/v1/equity/order/place`（POST）。** 它曾被漏掉，因为「路径存在但方法
    不对」时币安返回的是 **HTTP 404 + `Request method 'GET' is not supported`**，
    只看状态码会和「路径不存在」混为一谈。探测方法见 `references/trading-api.md`。
16. **买用 `notional`、卖用 `quantity`。** 不是同一个参数；买单传 `quantity` 会被忽略并报缺 `notional`。
17. **手续费固定 $0.17/笔，与金额无关。** 实测多笔成交（从最小额到数百美元）全部是 0.17。
    **别按比例估** —— $10 的单按比例会估成 $0.018，差一个数量级；反过来 $5 的最小单实付 3.4%。
    预览必须把固定费和它的占比显式打出来。
18. **下单额 $5 ~ 约 $100 万。** 低于 $5 报 `Order amount is below the minimum.`；
    币安的校验顺序是「缺参数 → 标的存在 → 限额 → 余额」，所以限额错误不会透露余额。
19. **不支持卖空，且只有 `free` 能卖。** `get-funding-asset` 的 `locked` 是**被挂单锁住**的部分
    （实测见过某标的约 10% 的数量被锁）。平仓只卖 free，并且要在输出里点明还有多少被锁。
20. **没有查挂单的接口**（`equity/order/open`、`openOrders`、`active`、`pending` 等全 404）。
    想知道有没有挂着单，只能看 `locked`。
21. **资金划转无权限（401）—— 这个 skill 搬不了钱。** 充值 / 划转必须用户在币安端自己做。
    而且**股票账户的现金余额也查不到**，所以余额无法预检，只能把币安的报错原样透传给用户。
22. **⚠️ 币安不采纳我们传的 `newClientOrderId` —— 别拿它做幂等或反查。**
    （本条更正了早先的记载，早先那句是错的。）实测：`place` 请求里带
    `newClientOrderId=stk...`，回显的 `clientOrderId` 却是币安自己生成的
    **`def_...`**。拿我们那个 `stk...` 去 `order/detail` 查，一律
    `{"code": 486218, "msg": "Order not found"}`；用 `orderId` 查得到，
    用币安回显的那个 `def_...` 也查得到。
    **为什么这条最要命**：查不到是**必然**的，所以「按 cid 查不到 ⇒ 大概率没成交」
    这个推断从根上就是错的 —— 真实成交会被误报成没成交，直接诱导重复下单。
    现在 `_recover` 改成两级：先按 cid 试，查不到就退回**按最近成交匹配**
    （symbol + side + 时间窗），命中唯一才认，多笔命中就说「无法确定」。
    `place` 骨架响应里带的 `orderId` 是可靠的，补查一律优先用它。
23. **`order/history` 的分页参数是 `current` + `size`，不是 `page` 也不是 `limit`。**
    **`limit` / `page` / `pageNum` / `pageNo` 全都不被识别，且是静默忽略** —— 请求里的
    回显 `page`/`size` 仍是默认的 `1`/`20`，只有 `size` 超限时才报错。
    `size` **上限 100**（101 起报 `-1102 Mandatory parameter 'size' was not sent...`，
    这条报错有误导性，其实是超限不是没传）。实测：`size=100` OK、`size=101` 拒、
    `current=3&size=10` 正确返回第 3 页。
    **后果很严重**：只取一页等于只拿最近 20 笔，而回放是**从头累积**的 ——
    少了某标的的建仓买单，后面的卖单就成了孤儿，卖出收入会被整笔当成利润。
    实测某标的因此**把亏损算成了盈利**（符号翻转，误差比真实盈亏本身还大），
    而且会随新订单在窗口边缘进出而反复变化。
    `fetch_orders` 现在翻页取全，**且取不全就抛错，绝不静默算一个错的数**。
24. **`place` 的响应体是 `{"status": "S", "orderId": ...}` 这种骨架，没有任何成交字段。**
    `"S"` 不是 `FILLED`/`NEW` 那套状态码。**判断成没成交不能看 `status` 存不存在**，
    要看有没有真的带回 `filledQty`/`avgFilledPrice`/`filledTotal`（挂单时这几个是 `"0"`）。
    实测（卖单）：只看 `status` 会把已成交的单子显示成
    「状态 S / 数量 None / 均价 -」外加一个 ⏳，**看着像没成交 —— 而"像没成交"会
    直接诱导重复下单**。现在缺成交信息一律补查 `order/detail`，补查也失败时
    渲染成 ❓ 并明确写出「真实状态未知，先去 App 核对，不要重复下单」。
25. **买单的 `notional` 是【总扣款，含手续费】，不是"买股票的钱"。**
    实测结论（下列数值为示意）：下单 `notional=100.17`，成交
    `filledTotal=100.00` + `fee=0.17`，两者相加正好 100.17。
    份额也对得上：按 `notional/ask` 本该拿 `100.17/50.00 = 2.0034` 股，
    实际 `2.0000` 股，差的 `0.0034` 股 × 50.00 = **0.17**，正好是那笔手续费。
    所以 **`股数 = (notional − 0.17) / 成交价`**。
    推论：拿"账户余额全额"下单时，`notional` 直接填余额即可，**不要再自己减手续费**
    （减了就是白白少买 0.17；$10 那种小单上这是 1.7%）。

26. **同一个股票代币有【两种资产形态】，到账哪一种不由接口决定。**
    - `EQ_<代码>`：币安**内部账本**资产。`get-funding-asset` 的行里带
      `stockTicker` 字段（如 `{"asset":"EQ_BABA","stockTicker":"BABA"}`）。
      `/sapi/v1/capital/config/getall` 的 742 个资产里**一个 `EQ_` 都没有**。
    - `<代码>B`：**bStocks**，BSC 链上真实代币（有合约地址、可提现、可现货交易，
      如 `BABABUSDT`）。config 里名字以 `(bStocks)` 结尾，实测 **77 个**。
    另有两族**链上**代币：`<代码>on`（Ondo Finance，以太坊+BSC）和 `<代码>x`（CT_501）。
    **同一标的最多可有四种表示，分属不同提供商和账本，币安不会替你并账。**
    详细目录、公开查询接口、各家的 `multiplier` 陷阱见 `references/equity-api.md`。
    两者是**不同的资产**，不是同一笔的两种叫法。App 里可以互转（用户实测手动转过）；
    **API 侧转不了** —— Convert 的 6434 个对里含 `EQ_` 的 0 个，equity 侧 6 条转换路径全 404。
    **坑**：走 `/sapi/v1/equity/order/place` 买 BABA，`symbol=BABA`，
    成交后落到 `BABAB`(bStock) 而不是 `EQ_BABA`（`EQ_BABA` 只多了 1e-8 尘埃，
    两者相加正好等于成交量）。而同一接口早先买的 MUU 落的是 `EQ_MUU`。
    **光看接口推不出会落哪种**，所以买入加了两道闸（见下「买入的资产形态闸」）。
    识别资产时**不要按 `<代码>B` 硬猜** —— `ACB`/`BRK.B` 这类真实代码会被误伤；
    要用 config 里 `(bStocks)` 名单。
27. **各接口单次耗时差一个数量级，买入的「确认」路径必须最短。**
    实测（美股交易时段）：
    `capital/config/getall` **13.1s** ／ `equity/order/history` 2.6s ／
    `equity/market/quote` 1.3s ／ `asset/get-funding-asset` 1.2s。
    **`config/getall` 13 秒是最大的单项开销**，绝不能出现在下单前的关键路径上。
    所以：预览路径照旧做全部检查；**确认路径（真正下单那一步）只发 `place` 一次**，
    形态检查/盘口检查都不重做（预览时做过，且确认码与参数逐字段绑定）。
    另：`fetch_bstocks` 结果缓存 7 天到 `~/.cache/binance-stocks/bstocks.json`。
28. **`clientOrderId` 的前缀会变，且和通道有关 —— 疑似能区分下单来源。**
    实测某账户的全部成交：09-15 及以前全是 `and_` 前缀，**09-16 起变成 `def_`**；
    同一个 `KO` 两种前缀都出现过，所以**不是按标的**，是按时间/通道变的。
    已知落 `EQ_*` 的（MUU/KO/QQQ/TQQQ/TSLL/MU）都是 `and_`，落 bStock 的 BABA 是 `def_`。
    疑似 `and_`=安卓 App 下单、`def_`=API 下单，**但未证实** —— 这条只是线索，
    不要拿它当判据。真正可靠的判据是买入的两道闸。
29. **这套接口是币安【未公开文档】的 —— 别指望找到"官方正确接口"。**
    查过官方 `github.com/binance/binance-skills-hub`（全树 188 个文件）：
    官方 `binance/binance` skill 覆盖 30 个产品领域（Spot / Futures / Convert /
    Margin / Wallet / Earn / Loan / Pay / Sub-account …），**没有任何 stock /
    equity / RWA / bStocks / EQ_ 条目**。唯一沾边的是
    `binance-web3/binance-tokenized-securities-info`，但它是**只读**的
    （查 RWA 元数据 / 行情 / K 线），不能下单。
    所以本 skill 里这一整套接口都是**实测探测**出来的，遇到疑问只能继续探，
    不要假设存在一份权威文档能对照。

股票相关接口实测存在这些（其余路径均 404）：

| 接口 | 方法 | 参数 |
|---|---|---|
| `/sapi/v1/asset/get-funding-asset` | POST | 无（取持仓） |
| `/sapi/v1/equity/market/quote` | GET | `symbol` |
| `/sapi/v1/equity/order/history` | GET | `startTime`、`endTime`、`current`、`size`（≤100） |
| `/sapi/v1/equity/trade/history` | GET | `startTime`、`endTime`（逐笔成交） |
| `/sapi/v1/equity/order/detail` | GET | `orderId` 或 `clientOrderId`（二选一） |
| `/sapi/v1/equity/order/place` | POST | 见下「交易」节 |
| `/sapi/v1/equity/order/cancel` | POST | `orderId` |

K 线与时段（TradFi 永续，`fapi.binance.com`，均为公开接口）：

| 接口 | 参数 |
|---|---|
| `/fapi/v1/klines` | `symbol`、`interval`、`limit` |
| `/fapi/v1/tradingSchedule` | 无（美股交易时段表） |
| `/fapi/v1/exchangeInfo` | 无（筛 `TRADIFI_PERPETUAL` 找合约） |

## KDJ 输出契约

`kdj --json` 顶层按标的代码索引，每个标的一个扁平对象，**K/D/J 就是唯一的权威值**：

```json
{
  "KO": {
    "perp": "KOUSDT", "K": 33.9, "D": 29.67, "J": 42.35,
    "signal": "中性区", "basis": "tradingDays",
    "bars": 28, "closedDaysExcluded": 13,
    "range": "2026-08-06 ~ 2026-09-15",
    "lastBarDate": "2026-09-15", "lastBarClosed": false
  }
}
```

| 字段 | 说明 |
|---|---|
| `K` / `D` / `J` | KDJ(9,3,3)。K/D 初值 50，样本 ≥20 根时已收敛 |
| `signal` | `超买区` / `超卖区` / `中性区`，可能追加 `J 高位` / `J 低位` |
| `basis` | 恒为 `tradingDays`，标明口径 |
| `bars` | 参与计算的交易日数 |
| `closedDaysExcluded` | 被剔除的停市日数 |
| `lastBarClosed` | **false 表示最后一根尚未收盘，K/D/J 仍会变到 UTC 日结束** |

`--compare` 会额外附带 `compareAllBars`（含停市日的口径）供核对，正常调用不该用它。

## RSI 输出契约

`rsi --json` 与 KDJ 同构，每个周期给三件东西：

```json
{
  "KO": {
    "perp": "KOUSDT", "basis": "tradingDays",
    "bars": 30, "closedDaysExcluded": 12,
    "lastBarDate": "2026-09-16", "lastBarClosed": false,
    "rsi": {
      "6":  { "value": 50.97, "seedInfluence": 0.015, "reliable": true },
      "12": { "value": 52.58, "seedInfluence": 0.228, "reliable": false },
      "24": { "value": 55.28, "seedInfluence": 0.808, "reliable": false }
    }
  }
}
```

| 字段 | 说明 |
|---|---|
| `value` | RSI 值，标准 Wilder 定义（首值用 SMA 种子） |
| `seedInfluence` | 当前值有多大比例仍由初始种子决定，0~1。**低于 0.05 才建议采信** |
| `reliable` | `seedInfluence <= 0.05` 的布尔快捷方式 |

`--periods 6,12,24` 可换周期（默认即此）。样本不足时 `value` 为 `null`。

## 交易（建仓 / 平仓）

**只做市价单。** 两步确认，缺一不可：

```bash
python3 scripts/stocks.py buy  TSLA --usd 100                 # ① 预览，打印确认码
python3 scripts/stocks.py buy  TSLA --usd 100 --confirm 7a3f  # ② 码对了才真下单

python3 scripts/stocks.py sell KO --qty 1                     # 部分平仓
python3 scripts/stocks.py sell KO --all                       # 全部平仓
```

**流程要求（agent 必须遵守）**：先跑一次不带 `--confirm` 的，**把预览输出给用户看**，
用户认可后，再把预览里给出的确认码原样传回去。

确认码是**一次性**的、10 分钟过期，且**与预览时的下单参数逐字段绑定** ——
改了金额 / 数量 / 标的，旧码立刻失效（这也顺带防住了「用户批了 $100，agent 却下 $500」）。
它由本地随机生成、存在 `~/.cache/binance-stocks/pending.json`，**不是参数哈希**，
所以不跑预览就拿不到，agent 无法绕过用户自行下单。

**本地预检**（下单前拦掉，不用等币安报错）：

| 情况 | 行为 |
|---|---|
| 金额 < $5 | 拒绝（币安最小下单额） |
| 金额 > 单笔护栏（默认 $500） | 拒绝，需显式 `--max <值>` 放行 |
| 卖出超过持仓 | 拒绝（本地就能查，不必等币安） |
| 无有效盘口（bid/ask 为 0 或缺失） | 拒绝 |

**护栏对 `--all` 同样生效**：持仓市值超过 $500 时，平仓也要显式加 `--max`。

### 买入的资产形态闸（两道）

背景见上文第 26 条：接口决定不了到账是 `EQ_*` 还是 bStock，而买错在当时看不出来。

| 时机 | 行为 |
|---|---|
| **下单前** | 账户里已有 `EQ_<代码>` → 放行；只有 bStock 形态、或两种都没有 → **拦下**，除非加 `--allow-bstock` |
| **成交后** | 核对实际到账形态；落到 bStock 就**明确报警**，不闷声当成功 |

买入流程被拆成两段，**确认路径只发 `place` 一次请求**（见第 27 条）：
形态检查、盘口检查全在预览路径做，确认时不重做 —— 行情在动，下单前每多一次
1~13 秒的调用都是实打实的损失。

### 超时处理（重要）

**网络超时不等于下单失败。** `place` 请求异常时，脚本会用 `newClientOrderId` 反查这笔单子
到底存不存在，三种结果如实报出：

- 查到且已成交 → 按成交报
- 查到但未成交 → 按挂单报
- **查不到 → 明确提示「大概率没成交，但请先去币安 App 核对，不要直接重复下单」**

区分这一点是刻意的：谎报「失败」会让用户重复下单，比慢一点危险得多。

### 交易输出契约

预览态（`preview: true`，无 `orderId`，**未下单**）：

```json
{
  "preview": true, "symbol": "TSLA", "side": "BUY", "orderType": "MARKET",
  "notional": "100.00",
  "quote": {"bidPrice": "356.81", "askPrice": "357.08", "bidSize": 264, "askSize": 49},
  "estQty": "0.28010420", "estFee": "0.17", "feePct": 0.17,
  "confirmCode": "7a3f"
}
```

成交态：

```json
{
  "preview": false, "orderId": "...", "clientOrderId": "stk1758000000a1b2c3d4",
  "symbol": "TSLA", "side": "BUY", "orderType": "MARKET", "status": "FILLED",
  "filledQty": "0.2799", "avgFilledPrice": "357.18",
  "filledTotal": "100.00", "fee": "0.17"
}
```

卖单预览以 `quantity` / `estProceeds` / `remainingAfter` 代替 `notional` / `estQty`。
`confirmCode` 只在预览态出现。

## 排错

| 现象 | 原因 |
|---|---|
| `-1022 Signature for this request is not valid` | 密钥类型判断错了，或签名没覆盖实际发送的字符串 |
| `-2015 Invalid API-key, IP, or permissions` | Key 没有读取权限，或启用了 IP 白名单 |
| `Timestamp for this request is outside of the recvWindow` | `recvWindow` 太小，见上文第 5 条 |
| 持仓显示为空 | 查错账户了，股票代币在资金账户（见第 1 条） |
| `KeyError: 'price'` 之类的解析错误 | 接口报错被当成成功响应解析了，先打印原始响应 |
| KDJ 与交易软件对不上 | 币安 K 线含停市日，本工具已剔除；仍有差先看 `bars` 是否够 20 根 |
| KDJ 每次查都在变 | `lastBarClosed` 为 false，当日 K 线未收盘，属正常 |
| `接口返回空响应（HTTP 200 无内容）` | 标的不存在或该标的无此数据。币安对这个查询不返回错误码，只给空的 200 |
| `closedDaysExcluded` 比预期少 | 历史假期滑出了时段表窗口且不在缓存里，见第 12 条 |
| RSI 数值看着不可信 | 看 `seedInfluence`，见第 13 条 |
| 找不到 K 线接口 | 股票代币没有，要走 TradFi 永续，见第 9 条 |
| `Insufficient balance. Please deposit and try again.` | 余额不足。先到币安端充值 / 划转 USDC（现金余额查不到，无法预检） |
| `Order amount is below the minimum.` | 低于 $5 最小下单额，见第 18 条 |
| `Order quantity exceeds the maximum.` / `Order amount exceeds the maximum.` | 超过币安上限，见第 18 条 |
| 确认码无效 / 已过期 / 与参数不符 | 一次性 + 10 分钟有效期；改了参数也会失效。重新跑预览取新码 |
| 下单超时 / 无响应 | 脚本会按 `newClientOrderId` 反查；查不到时先去 App 核对，**不要直接重复下单** |
| `You are not authorized to execute this request.` | API key 没有该权限（划转类接口全部如此，见第 21 条） |
| 下单回显 `状态 S / 数量 None / 均价 -` | `place` 的骨架响应，不是「没成交」。见第 24 条；脚本已改成自动补查详情 |
| `成交流水分页没有生效` | 翻页参数失效时自保报错，见第 23 条。别绕过这个检查去算盈亏 |
| `成交流水没取全` | `total` 比实际取到的多（时间窗截断）。宁可不出数，也不出一个错的数，见第 23 条 |
| 已实现盈亏突然变大/变小甚至消失 | 老版本的静默截断，见第 23 条。先确认跑的是修好后的版本 |
| 买入被拦下「避免买错资产形态」 | 账户里没有 `EQ_<代码>`。见第 26 条与「买入的资产形态闸」；确认要 bStock 才加 `--allow-bstock` |
| 买完提示「到账的是 bStock 形态」 | 没买错，但到账形态不是 `EQ_*`。持仓报表能看见也能卖；要转成 `EQ_*` 目前只能去 App 手动转 |
| 买入很慢（十几秒） | `capital/config/getall` 单次 13s，见第 27 条。只在 bStocks 名单缓存过期后的首次调用出现 |
| 持仓报表里看不到某个标的 / 卖出说「未持有」 | 大概率买成了 bStock。见第 26 条：两种形态是两个资产，不会自动合并。跑 `get-funding-asset` 看原始行 |
| 想用 API 把 bStock 转成 `EQ_*` | 做不到。Convert 无 `EQ_` 对，equity 侧 6 条转换路径全 404。只能去 App 手动转 |
| `multiplier` 不为 1 的代币，数量对不上股数 | `on`/`x` 族是分数股+股息再投资，1 代币 ≠ 1 股，见 `references/equity-api.md` |

## 扩展

`binance_client.py` 是通用的签名客户端，可打任意币安接口：

```python
from binance_client import Client
cli = Client()
cli.request("GET", "/api/v3/account")                      # 签名请求
cli.request("GET", "/api/v3/time", signed=False)           # 公开请求
cli.request("POST", "/sapi/v1/asset/get-funding-asset")    # POST 签名请求
```

更多参考：

- `references/equity-api.md` —— 股票代币接口探测记录、26 个实测 404 的路径、
  **四种资产表示（`EQ_` / `<代码>B` / `on` / `x`）的对照与识别方法**、官方无此产品的查证
- `references/klines-and-sessions.md` —— TradFi 永续、K 线、交易时段表、停市日判定
- `references/indicators.md` —— KDJ / RSI 的公式、收敛推导与验证用例
- `references/trading-api.md` —— 下单 / 撤单 / 查单接口、限额与固定费率、接口探测方法
