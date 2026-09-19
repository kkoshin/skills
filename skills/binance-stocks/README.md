# binance-stocks

一个 [Claude Code Skill](https://docs.claude.com/en/docs/claude-code/skills)，用于查询**和交易**币安股票代币（`EQ_*`）—— 持仓、移动加权成本、实时行情、已实现/浮动盈亏，以及 KDJ / RSI 指标和市价建仓 / 平仓。

纯 Python 标准库，无第三方依赖，无需 `pip install`。**`buy` / `sell` 是真实下单、真实资金。**

> ## ⚠️ 这个 skill 会用真实资金下单
>
> `buy` / `sell` 是真的发单，不是模拟。使用前请确认你清楚这一点。
>
> - **只做市价单**，通常即时成交，没有反悔窗口。
> - **两步确认**：先跑预览拿一次性确认码，把预览给人看过之后，再拿码执行。确认码与下单参数逐字段绑定，改了金额 / 数量 / 标的历史码立刻失效。
> - **默认单笔护栏 $500**，超过需显式 `--max <值>` 放行（`--all` 平仓同样受此限制）。
> - **最小下单额 $5**，手续费固定 **$0.17/笔**（与金额无关，$5 的单费率就是 3.4%）。
>
> 本项目按现状提供，不对任何交易损失负责。

## 这个 skill 做什么

| 命令 | 作用 |
|---|---|
| `positions`（默认） | 持仓表 + 实时行情 + 浮动/已实现盈亏 |
| `orders` | 全部成交流水（自动翻页取全） |
| `quote <代码>` | 单只实时买卖盘 |
| `kdj [代码]` | 日 K KDJ(9,3,3)，**仅交易日口径** |
| `rsi [代码]` | 日 K RSI，带收敛度评估 |
| `buy <代码> --usd <额>` | 市价建仓（两步确认） |
| `sell <代码> --qty <量> \| --all` | 市价平仓（两步确认） |

几个刻意的设计选择：

- **成本价用移动加权成本法从完整成交流水回放得出**，不依赖币安的汇总字段 —— 后者在流水被截断时会给出严重错误的数字。
- **流水取不全就报错，绝不静默算一个错的数。**
- **KDJ / RSI 一律剔除停市日**（周末 + 美股假期）后才计算。币安永续 24 小时在跑，停市日照样出 K 线但量极低，这些 bar 会把指标推离真实水平。
- **RSI 附带 `seedInfluence`** —— 币安永续历史普遍很短，RSI24 常常远未收敛，光看数值会误判。低于 0.05 才建议采信。
- 下单超时**不谎报失败**：会按订单反查真实状态，查不到时明确说"状态未知，先去 App 核对，不要重复下单"。

## 快速开始

### 1. 安装 skill

推荐用 [skills](https://github.com/vercel-labs/skills) CLI：

```bash
npx skills add kkoshin/skills --skill binance-stocks
```

手动安装（把本目录放到 Claude Code 的 skills 路径下，或软链）：

```bash
git clone https://github.com/kkoshin/skills.git ~/src/skills

# 个人级：所有项目可用
mkdir -p ~/.claude/skills
ln -s ~/src/skills/skills/binance-stocks ~/.claude/skills/binance-stocks

# 或项目级：只在该项目可用
ln -s ~/src/skills/skills/binance-stocks ~/my-project/.claude/skills/binance-stocks
```

Windows 下建议直接复制目录（软链需开发者模式）。

### 2. 在 Claude Code 里触发

装好后直接用自然语言问即可，例如「我的币安股票持仓怎么样」「看看 KO 的 KDJ」。skill 的描述会让 Claude 在相关话题下自动加载。

涉及下单时，Claude 会先跑预览给你看，拿到确认码后才真下单。

### 3. 前置条件

这几条是硬性的，缺一条就跑不出正确结果：

1. **币安账号，且已开通股票代币。** 股票代币在**资金账户**（Funding），不在现货账户 —— 只查现货会得到"空账户"的错误结论。
2. **API Key 需有读取权限 + 股票交易权限。**
3. **不要启用 IP 白名单**（会导致 `-2015 Invalid API-key, IP, or permissions`）。
4. **网络能直连 `api.binance.com` 与 `fapi.binance.com`。** 币安有地域限制，部分地区需自行解决网络可达性；股票代币本身也有地区可用性差异。

> 本 skill **搬不了钱**：充值 / 划转接口无权限（401），股票账户的现金余额也查不到，必须你在币安端操作。

### 4. 直接跑脚本（可选）

在 skill 目录下直接跑：

```bash
python3 scripts/stocks.py            # 持仓 + 行情 + 盈亏（默认，人读）
python3 scripts/stocks.py kdj KO     # 指定标的的 KDJ
python3 scripts/stocks.py quote KO   # 单只实时买卖盘

# 交易是两步确认：先预览拿码，看过之后再执行
python3 scripts/stocks.py buy TSLA --usd 100
python3 scripts/stocks.py buy TSLA --usd 100 --confirm 7a3f
```

完整命令、输出契约与 `--json` / `--compare` / `--max` / `--allow-bstock` 等参数见 [SKILL.md](SKILL.md)。

## 配置

只需环境变量：

| 变量 | 必需 | 说明 |
|---|---|---|
| `BINANCE_API_KEY` | ✅ | API Key |
| `BINANCE_SECRET_KEY` | ✅ | **密钥内容本身，或指向 PEM 文件的路径**，两种都支持 |
| `BINANCE_API_BASE` | | 默认 `https://api.binance.com` |
| `BINANCE_FUTURES_API_BASE` | | 默认 `https://fapi.binance.com`（KDJ/RSI 用） |
| `XDG_CACHE_HOME` | | 缓存根目录，默认 `~/.cache` |

**签名类型自动识别**：HMAC-SHA256 与 Ed25519（PKCS#8）都支持，无需配置。注意 Ed25519 的签名是 base64 编码、HMAC 是 hex —— 自己写客户端时这点最容易错。

缓存写在 `~/.cache/binance-stocks/`，会自动创建：

| 文件 | 内容 | 说明 |
|---|---|---|
| `no_trading.json` | 累积的停市日区间 | 交易时段表只覆盖前后约一周，历史假期滑出窗口后若不缓存，同一个历史日期的 KDJ 会随查询时间变化 |
| `bstocks.json` | bStocks 资产名单 | 缓存 7 天。它背后的接口单次耗时约 13 秒，删掉后首次查询会明显变慢 |
| `pending.json` | 待确认的下单 | 权限 `0600`，确认码 10 分钟过期、一次性 |

## 已知边界

- **K 线历史很短**，等于合约上线时间（有的标的仅 14 根日 K）。少于 20 根时 K/D 仍被初值 50 牵引，结论不可用。
- **早于首次运行、且已滑出时段表窗口的历史假期补不回来**，冷启动时会被计入（实测单个假期使 K 偏移约 0.7）。此后每个假期都会被捕获并永久缓存。
- **没有查挂单的接口**（相关路径全部 404）。想知道有没有挂着单，只能看 `locked` 数量。
- **股票账户的现金余额查不到**，所以下单前无法预检余额，只能把币安的报错原样透传。
- **这套接口是币安未公开文档的。** 官方 `binance-skills-hub` 覆盖的 30 个产品领域里没有任何 stock / equity / RWA 条目，官方 `binance-cli` 也不支持股票代币。本 skill 里的接口全部是实测探测出来的。

### 买入的资产形态闸

同一个股票在币安有**多种资产形态**，且**光看接口推不出会到账哪一种**：

- `EQ_<代码>` —— 币安内部账本资产（`EQ_BABA`）
- `<代码>B` —— bStocks，BSC 链上真实代币（`BABAB`），可提现、可现货交易
- `<代码>on` / `<代码>x` —— 另两族链上代币，分属不同提供商

**它们是不同的资产，币安不会替你并账。** 实测同一个下单接口，买 MUU 落到 `EQ_MUU`，买 BABA 却落到 `BABAB`。

所以买入加了两道闸：**下单前**账户里没有 `EQ_<代码>` 就拦下（确认要 bStock 才加 `--allow-bstock`）；**成交后**核对实际形态，落成 bStock 会明确报警。API 侧无法把 bStock 转成 `EQ_*`，只能去 App 手动转。

## 安全

- 私钥全程只在内存里，**不落盘**（Ed25519 是纯 Python RFC 8032 实现，不需要把密钥写成临时文件交给 `openssl`）。
- 待确认的下单文件 `pending.json` 以 `0600` 写入。
- 确认码本地随机生成、不是参数哈希 —— **不跑预览就拿不到码**，agent 无法绕过用户自行下单。
- 建议给 API Key 只开必要权限，并按自身情况考虑 IP 白名单（本工具默认需要关闭，见「前置条件」）。

## 目录结构

```
binance-stocks/
├── SKILL.md                      # skill 入口：用法、29 条实测事实、输出契约、排错表
├── README.md                     # 本文件
├── .gitignore
├── scripts/
│   ├── stocks.py                 # CLI 入口（持仓 / 指标 / 交易），纯标准库
│   └── binance_client.py         # 通用签名客户端，可打任意币安接口
└── references/
    ├── equity-api.md             # 股票代币接口探测记录、四种资产形态对照
    ├── trading-api.md            # 下单 / 撤单 / 查单、限额与费率
    ├── klines-and-sessions.md    # TradFi 永续、K 线、交易时段表
    └── indicators.md             # KDJ / RSI 公式、收敛推导与验证用例
```

## 文档

- [SKILL.md](SKILL.md) —— 完整工作流：29 条实测事实、输出契约、两步确认下单、排错表
- [股票代币接口](references/equity-api.md) —— 探测记录、26 个实测 404 的路径、四种资产形态对照
- [交易接口](references/trading-api.md) —— 下单 / 撤单 / 查单、限额与费率
- [K 线与交易时段](references/klines-and-sessions.md) —— TradFi 永续、K 线、交易时段表
- [指标公式](references/indicators.md) —— KDJ / RSI 公式、收敛推导与验证用例

## License

MIT
