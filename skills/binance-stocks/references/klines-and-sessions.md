# K 线与交易时段（TradFi 永续）

股票代币本身**没有 K 线接口**（`sapi/v1/equity/market/klines` 等实测全 404）。
但币安有 191 个 `TRADIFI_PERPETUAL` 永续合约，其中 183 个标的是股票，走标准合约接口，
可以取到完整的 OHLCV。这是目前从币安拿股票 K 线的唯一途径。

## 找到对应的合约

```
GET https://fapi.binance.com/fapi/v1/exchangeInfo        (公开)
```

在 `symbols[]` 里筛 `contractType == "TRADIFI_PERPETUAL"`。关键字段：

| 字段 | 说明 |
|---|---|
| `contractType` | `TRADIFI_PERPETUAL` = 传统金融永续；普通加密永续是 `PERPETUAL` |
| `underlyingType` | `EQUITY`(美股) / `HK_EQUITY` / `KR_EQUITY` / `CN_EQUITY` / `COMMODITY` / `INDEX` / `PREMARKET` |
| `status` | `TRADING` / `SETTLING` |

股票类合约（183 个）覆盖 AAPL/TSLA/NVDA/MSFT/GOOGL/META/AMZN/QQQ/SPY 等，
也包括 `KOUSDT`、`MUUSDT`、`MUUUSDT`、`TSLLUSDT` 这类。

**代币代码 → 合约代码的映射就是 `{代码}USDT`**（`EQ_KO` → `KO` → `KOUSDT`）。
注意 `MU` 和 `MUU` 是两个独立的标的，别混。

价差交叉验证：`EQ_KO` 代币 bid 88.08 时 `KOUSDT` 永续 88.31，同一标的的两个市场。

## 取 K 线

```
GET /fapi/v1/klines?symbol=KOUSDT&interval=1d&limit=1000      (公开，无需签名)
```

返回标准数组：`[开盘时间, 开, 高, 低, 收, 量, 收盘时间, ...]`。

**历史深度因合约而异**，等于该合约的上线时间，而且普遍很短：

| 合约 | 日K根数 | 起始 |
|---|---|---|
| TSLAUSDT | 231 | 2026-01-28 |
| MUUSDT | 162 | 2026-04-07 |
| QQQUSDT | 163 | 2026-04-06 |
| MUUUSDT | 62 | 2026-07-16 |
| KOUSDT | 41 | 2026-08-06 |
| TSLLUSDT | 14 | 2026-09-02 |

样本少于 20 根时 KDJ 的 K/D 仍被初值牵引，结论不可用。

## 交易时段表

```
GET /fapi/v1/tradingSchedule        (公开，无需签名)
```

返回 `marketSchedules`，按市场分组：`EQUITY`、`HK_EQUITY`、`KR_EQUITY`、
`CN_EQUITY`、`COMMODITY`、`FX`。每组的 `sessions[]` 是 `{startTime, endTime, type}`，
`type` 取值 `REGULAR` / `PRE_MARKET` / `AFTER_MARKET` / `OVERNIGHT` / `NO_TRADING`。

**接口只覆盖查询日前后各一周。**

### 关键发现：美股交易日 = UTC 日

美股 `EQUITY` 的四段首尾相接，正好铺满一个 UTC 日：

```
OVERNIGHT     00:00 ~ 08:00 UTC    (美东前一日 20:00 ~ 04:00)
PRE_MARKET    08:00 ~ 13:30 UTC
REGULAR       13:30 ~ 20:00 UTC    (美东 09:30 ~ 16:00)
AFTER_MARKET  20:00 ~ 24:00 UTC    (美东 16:00 ~ 20:00)
```

美东 20:00 = UTC 00:00，所以币安把美股交易日对齐到了 UTC 日边界。
**一根 UTC 日 K = 一个完整的美股交易日（含盘前盘后夜盘）。**

停市区间（周末、美股假期）标记为 `NO_TRADING`，例：
`2026-09-11 20:00 ET ~ 2026-09-13 20:00 ET` = `09-12 00:00 ~ 09-14 00:00 UTC`。

### 停市日不是没数据，是数据很稀薄

周末期间永续**仍在交易**，日 K 有量，但极低。实测按星期分组均量：

| 合约 | 工作日均量 | 周末均量 | 周末/工作日 |
|---|---|---|---|
| KOUSDT | 6178 | 2824 | 45.7% |
| MUUUSDT | 4,000,683 | 291,566 | **7.3%** |

所以直接对全量 K 线算指标，会被这些稀薄的停市日 bar 污染 —— 它们在 RSV 窗口里
占位，却不对应任何正股行情。

### 判定停市日的正确做法

**不要硬编码「周一到周五」**，那样会把美股假期当成交易日。应该用 `tradingSchedule`：
某 UTC 日与所有 `NO_TRADING` 区间的交集时长等于整日时长，即为停市日。
落在时段表覆盖范围之外的 K 线，再退回按 UTC 星期判断。

实测差异：KOUSDT 在 2026-08-06 ~ 09-15 区间内，硬编码会得到 29 个交易日，
时段表得到 **28** 个 —— 差的那天是 **2026-09-07 劳动节**，时段表正确识别为全天 `NO_TRADING`。

### 时段表窗口会滑动，必须配缓存

`tradingSchedule` **只覆盖查询日前后各约一周**。实测某次查询：

```
时段表覆盖: 2026-09-08 ~ 2026-09-22 UTC   (前 8 天 / 后 5 天)
K 线范围:   2026-08-06 ~ 2026-09-16 UTC   → 42 根里 33 根在覆盖范围外
```

滑出窗口的历史日会退回按星期判断，于是 **同一个历史日期的 KDJ 随查询时间变化**：
劳动节 09-07 在 09-15 查询时被正确剔除（13 个停市日），到 09-16 再查时滑出窗口、
被误判为交易日（12 个停市日）。单个假期的影响实测 ΔK≈0.7、ΔD≈1.0。

修法是把见过的 `NO_TRADING` 区间累积到本地缓存并取并集：

```
~/.cache/binance-stocks/no_trading.json     (可用 XDG_CACHE_HOME 改)
```

- 每个假期在窗口内经过时被捕获，之后永久生效
- 缓存损坏或不可写时静默降级，不影响主流程
- **已知边界**：早于首次运行、且已滑出窗口的假期补不回来。缓存删除后退回冷启动状态

不要试图用成交量识别假期 —— 实测 KOUSDT 劳动节当天量是常值的 **78.5%**，与正常
交易日（08-31 为 75.6%、09-09 为 70.3%）无法区分；MUUUSDT 那天 21%，而周日
09-13 是 26.6%，同样无法区分。假期期间永续照常活跃，量与"是否开市"无关。
