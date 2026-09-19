# 币安股票代币（Tokenized Equity）接口探测记录

币安没有公开文档说明股票代币的接口面。下面这份是实测探测出来的，供后续币安新增接口时对照。

## 探测方法

在 `/sapi/v1/` 下枚举候选路径发 GET，用 HTTP 状态区分：

- **404** + `{"error": "Not Found", ...}` → 路径不存在
- **400** + `{"code": -1102, "msg": "Mandatory parameter '...' was not sent"}` → **路径存在**，缺参数
- **200** → 存在且可用

关键是 404 和 400 的区别 —— 币安对不存在的路径返回 Spring Boot 风格的 404，对存在的路径返回带 `code` 的业务错误。

```python
for p in candidates:
    code, body = call("GET", f"/sapi/v1/{p}", {})
    if code != 404:
        print(code, p, body.get("msg", ""))   # 存在
```

## 实测存在的接口

### `GET /sapi/v1/equity/market/quote`

需要 `symbol`。返回实时买卖盘：

```json
{"symbol": "KO", "bidPrice": "88.4", "askPrice": "88.43",
 "bidSize": 500, "askSize": 200}
```

注意没有 `lastPrice` / 时间戳字段，只有盘口。估值用 `bidPrice`（能卖出的价）。

**传不存在的 symbol 时返回 HTTP 200 + 空响应体** —— 没有错误码、没有消息，就是空的。
直接 `json.loads` 会抛 `JSONDecodeError`，所以 `binance_client.py` 在成功路径上也做了
空响应和非 JSON 内容的防护，统一转成 `BinanceError`。这是实测踩出来的，不是防御性编程。

### `GET /sapi/v1/equity/order/history`

需要 `startTime` / `endTime`（毫秒），可选 `limit`。返回：

```json
{"page": 1, "size": 20, "total": 2, "rows": [
  {"orderId": "...", "symbol": "MUU", "quote": "USDC", "side": "BUY",
   "orderType": "MARKET", "avgFilledPrice": "30.0000", "qty": "1.00000000",
   "notional": "30.17", "filledQty": "1.00000000", "filledTotal": "30.00",
   "fee": "0.17", "status": "FILLED",
   "createdAt": 1789000000000, "updatedAt": 1789000000000}
]}
```

`filledTotal` 是含费前的成交额，`fee` 单独给。限价单额外有 `limitPrice` 和 `session`（交易时段）。

### `GET /sapi/v1/equity/trade/history`

需要 `startTime` / `endTime`。比 order history 更细，一笔订单拆成多条执行：

```json
{"page": 1, "size": 20, "total": 20, "rows": [
  {"executionId": "...", "orderId": "a1b2c3d4-...", "symbol": "MUU",
   "quote": "USDC", "side": "BUY", "orderType": "MARKET",
   "price": "30.0000", "qty": "0.5", "total": "15.00",
   "executionAt": 1789000000000}
]}
```

### `POST /sapi/v1/asset/get-funding-asset`

取持仓的唯一途径。返回资金账户全部资产，股票代币以 `EQ_` 前缀出现：

```json
[{"asset": "EQ_KO", "stockTicker": "KO", "free": "1.00000000",
  "locked": "0", "freeze": "0", "withdrawing": "0", "btcValuation": "0"},
 {"asset": "EQ_MUU", "stockTicker": "MUU", "free": "2.00000000", ...}]
```

`stockTicker` 字段直接给出不带前缀的代码。`btcValuation` 恒为 `"0"`，不可用于估值。

## 实测 404 的路径

以下 26 个候选路径全部返回 404，即不存在：

```
equity/order/query        equity/order/get          equity/order/open
equity/order/quote        equity/order/fee          equity/market/ticker
equity/market/depth       equity/market/klines      equity/market/symbols
equity/market/time        equity/quote              equity/ticker
equity/price              equity/symbol             equity/symbols
equity/time               equity/config             equity/fee
equity/trading-session    equity/session            equity/position
equity/position/history   equity/summary            equity/overview
equity/account            equity/balance            equity/asset
```

**没有现金余额接口。** `equity/account`、`equity/balance`、`equity/position`、`equity/asset` 全部 404。
`/sapi/v1/asset/wallet/balance` 只给每个钱包的 **BTC 估值**汇总（`{"walletName": "Funding", "balance": "0.00123456"}`），
虽然能反映资金账户总市值，但拿不到 USDC 现金明细，也无法拆到单个标的。

## 股票期权：产品存在，API 面为零

**结论：币安有美股期权产品，但当前没有任何用户 API 能碰它。** 2026-09-17 实测。
产品是 2026-09-01 才上线的，接口有可能滞后开放，**将来要复查 —— 方法见本节末尾**。

### 产品背景（用来判断"以后会不会开放"）

币安 2026-09-01 上线美股期权，1,000+ 只美股/ETF，**仅限美国以外用户**。链路：

```
用户 → Nest Trading Limited（ADGM 持牌券商，介绍经纪）
     → Alpaca Securities LLC（美国自清算券商，执行/清算/结算/托管）
```

只做多（只能买 call/put，不能卖出开仓）、**仅限价单**、实物交割、美股时段 9:30–16:00 ET、
权利金用 USDC/USDT/BNB 支付。

**别把"Alpaca 有 Broker API"误读成"用户能 API 下单"。** Alpaca 的 Broker API 是给
Nest 这家券商用的基础设施，不是给终端用户的。币安没有把它开放成用户 API。

### 实测证据

**1. sapi 路径扫描：160 个候选，0 个存在。**

前缀 × 子路径的笛卡尔积，全部落到「路径不存在」：

```python
pres = ["equity", "stock", "stocks", "tradfi", "broker", "securities",
        "nest", "alpaca", "us", "tradfi-equity"]
subs = ["option", "options", "option/chain", "option/quote", "option/symbols",
        "option/position", "option/order", "option/order/place",
        "option/order/cancel", "option/expiry", "option/strike", "option/greeks",
        "option/exercise", "option/history", "option/account", "option/balance"]
# 10 × 16 = 160，逐个 GET /sapi/v1/{pre}/{sub}
```

扫描时必须带**阳性对照**（`/sapi/v1/equity/market/quote` 应回 400 缺 `symbol`），
否则「全 404」无法区分"真的不存在"和"探测代码坏了"。

**2. 代币化股票接口不认期权代码。** `/sapi/v1/equity/market/quote` 分别传：

| symbol | 结果 |
|---|---|
| `KO` | 200，正常盘口 `bid 87.54 / ask 87.57` |
| `KO260116C00090000` | 200 空响应 |
| `KO260116C00090000.US` | 200 空响应 |
| `KO 260116C00090000` | 200 空响应 |
| `KO_OPT` | 200 空响应 |
| `KO260116C00090000US` | 200 空响应 |

OCC 代码全格式都不认。这条查询是只读的，可以放心重复跑。

**3. 唯一的期权交易所 eapi 里没有股票。**

```
GET https://eapi.binance.com/eapi/v1/exchangeInfo
→ 1784 个期权合约 / 8 个标的：
  BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT DOGEUSDT XAUUSDT XAGUSDT
```

纯加密 + 贵金属，无股票。(注：`XAU`/`XAG` 是贵金属，不是股票。)

**坑：`binance_client.py` 打不了 eapi。** `Client.request()` 恒定带
`Content-Type: application/x-www-form-urlencoded` 头，而 eapi 会拒绝：

```
400 {'msg': 'Content type in request header should not be set for this endpoint'}
```

eapi 的公开接口要么改用裸 `urllib.request.Request` 不带该头，要么给 client 加一个
"不发 Content-Type" 的开关。本次探测走的是前者。

**4. 权限面根本没有股票期权这个开关。**

`GET /sapi/v1/account/apiRestrictions` 返回的权限位：

```
enableReading: True                 enableSpotAndMarginTrading: True
enableFutures: False                enableVanillaOptions: False
enableMargin: False                 enableWithdrawals: False
enableInternalTransfer: False       permitsUniversalTransfer: False
enablePortfolioMarginTrading: False enableFixApiTrade: False
```

`enableVanillaOptions` 指的是 **eapi 加密期权**（且当前为关）。**整个权限列表里没有
任何一项对应股票期权** —— 所以这不是"权限没开"，是接口不存在。

### 复查方法（币安开放接口后跑这个）

产品刚上线，接口滞后开放是可能的。复查就是重跑上面的扫描：

```python
# 1. 阳性对照必须先通过，否则结论无意义
# 2. 160 路径扫描，看有没有从 404 变成 400/200/EXISTS-POST
# 3. 注意"路径存在但方法不对"返回的是 404 + "method 'GET' is not supported"，
#    别和 {"error": "Not Found"} 混为一谈（见 trading-api.md 的探测方法）
# 4. 若出现 POST 接口，探参数时严守 trading-api.md 的"订单结构性不可能成交"纪律
```

另外可以顺带查 `/sapi/v1/account/apiRestrictions`：**如果币安新增了股票期权权限位，
那就是接口要开的信号。**

## 同一个股票有【四种资产表示】—— 买之前必须分清

这是这个产品最容易踩的坑：**标的符号只有一个（`BABA`），但资产的表示形式有四种**，
分属不同提供商、不同账本，互相之间不会自动合并。

| 形态 | 实例 | 所在 | 提供商 / 账本 | 怎么认 |
|---|---|---|---|---|
| `EQ_<代码>` | `EQ_BABA` | 币安内部账本 | 币安自营股票产品 | 资金账户行里带 `stockTicker` 字段；`capital/config/getall` 的 742 个资产里**一个都没有** |
| `<代码>B` | `BABAB` | BSC 链上 | **bStocks** | config 里名字以 `(bStocks)` 结尾（实测 77 个）；有 BSC 合约地址、可提现 |
| `<代码>on` | `BABAon` | 以太坊 + BSC | Ondo Finance | 见下方公开目录接口；`type=1` |
| `<代码>x` | `QQQx` | CT_501 链 | 又一家 | 见下方公开目录接口；`type=2` |

实测：走 `/sapi/v1/equity/order/place` 以 `symbol=BABA` 买入，成交后落到
**`BABAB`(bStock)**，而不是 `EQ_BABA`；`EQ_BABA` 只多了 1e-8 尘埃，两者相加**正好等于**
成交量。而同一接口早先买的 `MUU` 落地是 `EQ_MUU`。
**光看接口推不出会落哪种**，所以买入加了两道闸（见 SKILL.md「买入的资产形态闸」）。

### 怎么判断某资产是不是 bStock —— 不要按 `B` 后缀硬猜

`ACB`、`BRK.B` 这类**真实代码本身就带 B**，按后缀猜会误伤。权威判据是
`/sapi/v1/capital/config/getall` 里资产名以 `(bStocks)` 结尾：

```python
cfg = cli.request("GET", "/sapi/v1/capital/config/getall")   # 742 个资产，实测 13.1 秒
bstocks = {c["coin"] for c in cfg if str(c.get("name", "")).endswith("(bStocks)")}
# -> {'BABAB', 'MUUB', 'TSLAB', 'AAPLB', 'QQQB', 'COINB', ...}  共 77 个
```

**这个调用 13 秒**，所以 `fetch_bstocks()` 把它缓存 7 天到
`~/.cache/binance-stocks/bstocks.json`，并且**绝不出现在下单前的关键路径上**。

### 公开的代币化证券目录（免鉴权）

币安 Web3 钱包侧有一组公开接口，能看到 `on` / `x` 两族的目录
（**bStocks 不在这里面**，是另一套目录）：

```
GET https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/rwa/stock/detail/list/ai
```

每条含 `chainId` / `contractAddress` / `symbol` / `ticker` / `type` / `assetType` /
`multiplier`。`type=1` 为以太坊(1)/BSC(56)，`type=2` 为 CT_501。待
`User-Agent: binance-web3/1.1 (Skill)`。

⚠ **`multiplier` 不为 1 时，不要把数量当股数比。** 实测 `BABAon` 的 multiplier 是
`1.00891343515435611`（含股息再投资），即 **1 个代币 ≠ 1 股**。

### 官方 skills 仓库里没有股票交易产品

`github.com/binance/binance-skills-hub` 全树 188 个文件；官方 `binance/binance` skill
覆盖 30 个产品领域（Spot / Futures / Convert / Margin / Wallet / Earn / Loan / Pay …），
**没有任何 stock / equity / RWA / bStocks / EQ_ 条目**。

结论：**`/sapi/v1/equity/*` 是币安未公开文档的接口面**，不存在"官方正确接口没被找到"
这回事。唯一沾边的 `binance-web3/binance-tokenized-securities-info` 是**只读**的
（查 RWA 元数据 / 行情 / K 线），不能下单。

### 疑似与下单通道有关（未证实）

实测全部成交的 `clientOrderId` 前缀：**09-15 及以前全是 `and_`，09-16 起变成 `def_`**；
同一个 `KO` 两种前缀都出现过，所以**不是按标的**变的。落 `EQ_*` 的（MUU/KO/QQQ/TQQQ/
TSLL/MU）都是 `and_`，落 bStock 的 BABA 是 `def_`。疑似 `and_`=安卓 App、`def_`=API。
**但未证实，且存在反例**：09-18 经 API 卖出的 KO 是 `def_`，扣的却是 `EQ_KO`。
不要拿前缀当判据，可靠的只有买入的两道闸。

## 已知的坑

- **入金记录有 90 天窗口。** `/sapi/v1/capital/deposit/hisrec` 时间跨度超过 90 天直接报
  `-4047 Time interval must be within 0-90 days`，查不到更早的充值。
- **股票代币成交以 USDC 计价**，而现货/资金账户的现金是 USDT，两套账不要混。
- **TradFi 永续合约不是股票代币。** 币安另有 TradFi Perps 产品（美股/韩股/港股交易时段、
  `PRE_MARKET`/`AFTER_MARKET`/`OVERNIGHT` 会话），和 `EQ_*` 是两回事，接口也不同。
- **两种形态不会自动合并。** 同一标的的 `EQ_<代码>` 和 `<代码>B` 是**两个独立资产**，
  币安不会替你并账。持仓报表要把两边**加起来**才是真实仓位；卖出只能卖其中一个，
  `--all` 卖不掉另一份。实测见过 `EQ_<代码>` 里只挂着 1e-8 量级的尘埃，真实仓位全在对应的
  bStock 里 —— 只看 `EQ_` 会得出"没持仓"的结论，然后本地预检把单子挡在门口，根本到不了币安。
- **转换只能去 App 手动做。** Convert（闪兑）支持 6434 个货币对，**含 `EQ_` 的 0 个**；
  `equity/convert|deposit|transfer|subscribe|redeem|wrap` 六条路径全 404。
  API 侧没有任何办法把 bStock 转成 `EQ_*`。
- **`notional` 是总扣款含手续费**（实测 `notional` = `filledTotal` + `fee`，账户余额
  正好少了 `notional`）。所以「按余额全额买」时 `notional` 直接填余额，**不要再减 0.17**。
