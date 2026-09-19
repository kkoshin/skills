# 币安股票代币交易接口探测记录

`references/equity-api.md` 只覆盖了查询面。这份记录下单/撤单/查单的接口契约。
全部结论来自实测，**探测期间没有产生任何真实订单** —— 方法见下。

## 探测方法

### 1. 用 GET 找 POST 接口

上一轮探测漏掉了下单接口，因为只区分了 404 / 400。实际上币安对"路径存在但方法不对"
返回的是 **HTTP 404 + `Request method 'GET' is not supported'`** —— 状态码和 404 一样，
但消息体不同。只看状态码会把它和"路径不存在"（`{"error": "Not Found"}`）混为一谈。

```python
try:
    call("GET", f"/sapi/v1/{p}", {})
except BinanceError as e:
    msg = e.payload.get("msg", "")
    if "method 'GET' is not supported" in msg:
        print("存在，且是 POST 接口:", p)     # ← 关键判据
    elif e.payload.get("error") == "Not Found":
        print("不存在:", p)
```

**GET 不可能下单**，所以这一步天然安全。

### 2. 用缺参提示逐参数补全

`place` 的必填参数是这样一个个问出来的：先发一个空请求，币安回
`Mandatory parameter 'X' was not sent`，把 X 补上再发，直到不再报缺参。

参数字典是**枚举**时，传占位符会被报成 "was empty/null, or malformed"（和"没传"同一条消息），
所以占位符填进去看不出进展，要换成合法枚举值才能继续往下问。

### 3. 保证订单结构性不可能成交

这是这一步最重要的纪律。补参数的过程中参数集会越来越完整，
**一旦凑齐且取值合法，就是一个真实订单**。所以全程用两种不变式钉死：

| 不变式 | 用于 | 为什么不可能成交 |
|---|---|---|
| `symbol` 用不存在的 `___PROBE___` | 打 `place` 的参数结构 | 标的不存在，校验必挂在标的存在性上 |
| `side=SELL` + 卖**不持有**的标的（如 AAPL） | 探最小值 / 枚举值 | 卖出必须有持仓，必然 `Insufficient balance` |
| `notional` 取 `0` 或 `999999999` | 探金额边界 | 0 元买不了；9.99 亿超过上限 |

**永远不要**用"小额但合法"的值去试探（比如 `notional=1`）。那种请求能不能成交
完全取决于账户里有没有钱，而不是取决于你的意图。

## 实测接口

### `POST /sapi/v1/equity/order/place`

| 参数 | 说明 |
|---|---|
| `symbol` | 股票代码，不带 `EQ_` 前缀（`TSLA`，不是 `EQ_TSLA`） |
| `side` | `BUY` / `SELL` |
| `orderType` | `MARKET`（`LIMIT` 见下） |
| `notional` | **市价买入**用，金额（USDC）。`"0"` 报 malformed |
| `quantity` | **市价卖出**用，股数 |
| `newClientOrderId` | 可选，自定义订单号（幂等用） |
| `tradingSession` | **仅 LIMIT 需要**，见下 |

买用 `notional`、卖用 `quantity`，**不是同一个参数**。买单传 `quantity` 会被忽略并报缺 `notional`。

### `POST /sapi/v1/equity/order/cancel`

参数 `orderId`。传不存在的 id 返回 `Order not found or already filled. Unable to cancel.`
**注意不是 `clientOrderId`** —— 传它会被报成缺 `orderId`。

### `GET /sapi/v1/equity/order/detail`

参数 `orderId` **或** `clientOrderId`（二选一）。返回完整订单，含 `trades[]` 逐笔成交。
已用真实订单验证过 `clientOrderId` 查询可用 —— 这是**下单超时后对账**的唯一手段。

### 校验顺序

实测币安按这个顺序拒绝，写预检时不要假设别的顺序：

```
缺参数  →  标的存在性  →  金额/数量上下限  →  余额
```

所以"金额超上限"永远不会告诉你余额够不够；想探余额只能下合法单。

## 限额与费率（实测）

| 项 | 值 | 测法 |
|---|---|---|
| 最小下单额 | **$5.00** | 二分收敛：0.01507749 股被拒 / 0.01507750 股放行（AAPL $331.45） |
| 最大下单额 | **约 $1,000,000** | 同上二分 |
| 手续费 | **固定 $0.17 / 笔** | 见下 |

**手续费是固定的，与金额无关。** 实测多笔历史成交，成交额跨两个数量级，手续费**全部是 0.17**。
费率因此随金额急剧变化（下列数值为示意）：

```
成交额     费     占比
  $10    0.17   ← 1.700%
 $100    0.17   ← 0.170%
 $200    0.17   ← 0.085%
```

**别按比例估算。** 按 0.17% 算，$10 的单会估成 $0.018，实际 $0.17，差一个数量级。
反过来，$5 的最小单实付 3.4%，小额高频会被固定费磨损得很厉害 —— 预览必须把这笔费
及其占比显式打出来。

## 已知的坑

- **`clientOrderId` 和 `newClientOrderId` 是两个东西。** `place` 只认 `newClientOrderId`；
  传 `clientOrderId` 不会报"未知参数"，而是报 `Mandatory parameter 'clientOrderId' was not
  sent`（消息本身有误导性）。查询则相反，`detail` 用 `clientOrderId`。
- **不支持卖空。** 卖出超过持仓直接 `Insufficient balance. Please deposit and try again.`
- **只有 `free` 能卖。** `get-funding-asset` 里 `locked` 是**被挂单锁住**的部分，实测见过
  某标的约 10% 的数量被锁。平仓时如果只看总量会下单被拒。
- **没有查挂单的接口。** `equity/order/open` / `openOrders` / `active` / `pending` / `working`
  等全部 404。想看挂单只能看 `get-funding-asset` 里的 `locked`，或订单历史的 `status`。
- **LIMIT 单需要 `tradingSession`，参数名是 camelCase，取值 `RTH`。**
  传 `trading_session`（下划线）或 `REGULAR` 都会被报 `trading_session is required for LIMIT orders`。
  `RTH` = regular trading hours。本项目目前只做市价单，这条留给以后扩展。
- **资金划转没有权限。** `/sapi/v1/asset/transfer` 各种 type 全部 401
  `You are not authorized to execute this request.`。**这个 skill 搬不了钱** ——
  充值/划转必须用户在币安端自己做。
- **股票账户的现金余额查不到。** 没有 `equity/balance` 之类的接口，所以**余额无法预检**，
  只能把币安的报错原样透传给用户。`get-funding-asset` 里的 USDC 只是个提示，不是权威。

## 仍未搞清

- **`newClientOrderId` 是否真被币安采用**（而不是接受后忽略）。验证它必须成交一笔真实订单，
  所以等首笔实盘下单后回查 `detail` 才能确认。代码里已经按"会被采用"来用 ——
  万一不成立，超时对账会退化成"查不到"，届时 `_recover()` 会明确告诉用户去 App 核对。
- **股票账户的现金到底在哪个钱包。** 资金账户只有 `EQ_*`，现货是空的，
  但历史订单确实成交过（USDC 计价）。推断是资金账户的 USDC，未证实。
