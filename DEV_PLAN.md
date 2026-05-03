# 尾盘选股策略回测开发计划

## 背景

`tail_screener.py` 是一个实盘尾盘选股策略脚本，每天 14:30 通过 AmazingData SDK (TGW网关) 获取分钟K线，聚合成当日OHLCV数据，计算技术指标并结合 Kronos AI 预测进行综合打分选股。

本项目目标是将其转化为可回测脚本，使用本地可用的数据源替代 AmazingData，支持历史数据回测验证策略有效性。

## 数据源架构

### 原始数据源 (tail_screener.py 使用)

| 数据 | 来源 | 说明 |
|------|------|------|
| 交易日历 | `AmazingData.BaseData.get_calendar()` | TGW网关 |
| 分钟K线 | `AmazingData.MarketData.query_kline(period=10000)` | TGW网关，`period=10000` 表示分钟线 |
| 实时快照 | TGW Snapshot | 实时行情 |

### 替代数据源 (回测使用)

| 数据 | 来源 | 接口 |
|------|------|------|
| 历史日线 | StockWinner SQLite | `/api/v1/ui/databases/kline/tables/kline_data/data` |
| 1分钟K线 | StockWinner + TGW | `/api/v1/ui/{account}/market/kline?period=1m` |
| Kronos模型 | 本地 | `~/.openclaw/workspace/Kronos/models/` |

**关键约束**: TGW 同一时刻只能一个连接，所有 API 调用必须**串行**，不可并发。

## 核心策略逻辑

### 1. 技术指标计算 (与 tail_screener.py 一致)

- **RSI(14)**: Wilder 平滑法
- **BOLL 位置**: 20日布林带相对位置 `(close-lower)/(upper-lower)*100`
- **ADX(14)**: 方向移动指数
- **MA 金叉**: MA5 > MA20
- **MACD**: EMA12 - EMA26，判断 DIF > DEA
- **尾盘拉升**: `close > open * 1.002`

### 2. 打分系统

| 条件 | 分值 |
|------|------|
| 涨幅 1%~5% | +20 |
| RSI 40~65 | +20 |
| BOLL 30%~70% | +10 |
| MA 金叉 | +10 |
| 尾盘拉升 | +15 |
| ADX >= 25 | +10 |
| MACD 多头 | +5 |

### 3. 过滤条件

- 涨幅 < 0.5% 或 > 9% → 剔除
- RSI < 20 或 > 80 → 剔除
- BOLL < 10% 或 > 85% → 剔除

### 4. 综合评分

```
有 Kronos:  composite = tail_score × 0.5 + kronos_pred × 2
无 Kronos:  composite = tail_score × 0.5
```

### 5. Kronos 预测

- 模型: `Kronos-small` (256 dim, 6 layers)
- Tokenizer: `Kronos-Tokenizer-base` (256 dim, 4 layers)
- 输入: 截止到 T-1 的日线 OHLCV
- 输出: 未来 5 个交易日预测
- 参数: `pred_len=5, T=1.0, top_p=0.9, sample_count=10`

## 回测交易逻辑

### 参数

- **回测区间**: 2026-04-01 ~ 2026-04-30
- **初始资金**: 100,000 元
- **买入规则**: 14:30 筛选，等权买入综合评分 TOP 3 (不足3只则全买)
- **卖出规则**: 次日 10:00 前盈利 > 3% 则止盈卖出，否则 10:00 无条件平仓
- **手数**: 按 100 股取整

### 价格确定

| 操作 | 时间 | 数据来源 |
|------|------|---------|
| 买入 | 14:30 | 第 210 根 1分钟 bar 的 close |
| 卖出止盈 | 9:30-10:00 | 第 0~30 根 1分钟 bar 的 close，首次盈利 >= 3% 时卖出 |
| 卖出平仓 | 10:00 | 第 30 根 1分钟 bar 的 close |

### 分钟线到日线的转换

tail_screener.py 在 14:30 运行时，`query_kline` 返回的是 9:30-14:30 的分钟线。通过 `groupby('date').agg()` 聚合：

```python
daily = df.groupby('date').agg(
    open=('open', 'first'),   # 9:30 开盘价
    high=('high', 'max'),      # 当日最高
    low=('low', 'min'),        # 当日最低
    close=('close', 'last'),   # 14:30 收盘价 (非全天收盘)
    volume=('volume', 'sum'),  # 累计成交量
    amount=('amount', 'sum')   # 累计成交额
)
```

回测中，使用当日 1分钟线的前 210 根 bar (9:30-14:30) 模拟这一过程。

## 文件结构

```
tail-stock-selection/
├── backtest.py              # 回测脚本 (主文件)
├── backtest_report.json     # 回测报告 (自动生成)
├── tail_screener.py         # 原始实盘脚本
├── stock_codes.json         # 股票代码配置
├── DEV_PLAN.md              # 本开发计划
├── kronos_integration.py    # Kronos集成相关
└── requirements.txt         # Python依赖
```

## 实现步骤

### Phase 1: 数据层
- `api_get()`: HTTP GET 封装，调用 StockWinner 8080 API
- `get_trading_dates()`: 从 SQLite 提取交易日
- `fetch_daily_from_sqlite()`: 获取历史日线
- `fetch_minute_data()`: 获取当日1分钟线

### Phase 2: 指标层
- 技术指标计算函数 (RSI/ADX/BOLL/MA/MACD)
- `get_tail_score()`: 打分
- `score_and_filter()`: 打分+过滤

### Phase 3: Kronos 集成
- 启动时加载 Kronos 模型
- `predict_kronos()`: 对历史日线数据进行预测

### Phase 4: 回测引擎
- 串行遍历交易日
- 对每只股票: 获取数据 → 计算指标 → Kronos → 打分
- 买入/卖出逻辑
- 资金/持仓管理

### Phase 5: 报告生成
- 买卖日志
- 统计指标 (胜率/盈亏比)
- 每日净值曲线
- JSON 格式输出

## 注意事项

1. **TGW 单连接限制**: 所有 API 调用必须串行，不能并发
2. **分钟线无时间戳**: API 返回的分钟线 `trade_date` 为空，按固定顺序排列 (每天240根)
3. **Kronos 预测耗时**: 每只股票约 1-3 秒，70只 × 20天 = 1400次预测
4. **API 调用频率**: 70只 × 20天 × 2次 (日线+分钟线) = 2800次 + 1400次Kronos
5. **预计总耗时**: 约 2 小时

## 验证方法

1. 单日回测 (2026-04-30)，对比 tail_screener.py 输出
2. 全月回测 (2026-04-01 ~ 04-30)
3. 检查买卖逻辑、资金变化一致性
