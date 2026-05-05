#!/usr/bin/env python3
"""
尾盘选股策略回测脚本 v4 (无 Kronos)
- 对比版本: 去掉 Kronos 预测，仅使用技术指标打分
- 策略: 止盈+5%、止损-2%、尾盘拉升阈值0.5%、OBV成交量确认
- 卖出: 延长至14:00平仓
"""
import sys, os, json, time, statistics
from datetime import datetime, timedelta
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ─── AmazingData SDK ─────────────────────────────────────────
import AmazingData as ad
from AmazingData.utils.constant import Period

AMAZING_USER = '210400098788'
AMAZING_PASS = '210400098788@2026'
AMAZING_HOST = '101.230.159.234'
AMAZING_PORT = 8600

_market = None
_base = None
_daily_cache = {}

# (Kronos 已移除 — 对照版本)

# ─── 策略参数 ────────────────────────────────────────────────
TAKE_PROFIT_PCT = 5.0       # 止盈阈值 (放宽至 +5%)
STOP_LOSS_PCT = -2.0        # 止损阈值 (不变)
MIN_COMPOSITE = 15.0        # 最低综合分门槛
HOLD_DAYS = 2               # 持仓天数 (T+N)
KRONOS_WEIGHT = 1.0         # Kronos 权重
TAIL_SCORE_WEIGHT = 0.6     # tail_score 权重
SELL_BAR = 180              # 14:00 强制平仓 (下午第60根K线 = 13:00 + 60min)

INITIAL_CAPITAL = 100000.0

WATCHLIST = [
    # 沪深300核心
    '600519.SH','601318.SH','600036.SH','601398.SH','600900.SH',
    '601012.SH','300750.SZ','300274.SZ','600089.SH','601166.SH',
    '600887.SH','603259.SH','300760.SZ','002475.SZ','300059.SZ',
    '002594.SZ','600276.SH','300122.SZ','600030.SH','601888.SH',
    '600585.SH','601668.SH','600028.SH','601857.SH','600690.SH',
    '601186.SH','600048.SH','600837.SH','601601.SH','601336.SH',
    '002415.SZ','002027.SZ','600009.SH','600104.SH','601766.SH',
    '601989.SH',
    # 半导体
    '002371.SZ','688981.SH','603986.SH','688008.SH','002049.SZ',
    '603501.SH','688012.SH','002185.SZ','600584.SH',
    # AI算力
    '300496.SZ','002230.SZ','300024.SZ','688521.SH',
    '688256.SH','688787.SH','603019.SH','300308.SZ','002402.SZ',
    # 商业航天
    '688297.SH','600893.SH','002025.SZ','600118.SH','300159.SZ',
    '603256.SH','600150.SH','002389.SZ','688148.SH','002265.SZ',
    # 医药
    '300015.SZ','600196.SH','000661.SZ','002007.SZ','300142.SZ',
    '600211.SH','300347.SZ','002422.SZ',
    # 新能源/光伏
    '002129.SZ','601012.SH','600438.SH','002202.SZ','300014.SZ',
    '603659.SH','688005.SH',
    # 消费/白酒
    '000858.SZ','000568.SZ','600809.SH','002304.SZ','000596.SZ',
    '600519.SH','603288.SH',
    # 军工
    '000768.SZ','601989.SH','002013.SZ','600862.SH','002238.SZ',
    '300115.SZ',
    # 金融科技
    '002230.SZ','300033.SZ','600570.SH','002152.SZ','300253.SZ',
]

NAME_MAP = {
    '600519.SH':'贵州茅台','601318.SH':'中国平安','600036.SH':'招商银行',
    '601398.SH':'工商银行','600900.SH':'长江电力','601012.SH':'隆基绿能',
    '300750.SZ':'宁德时代','300274.SZ':'阳光电源','600089.SH':'特变电工',
    '601166.SH':'兴业银行','600887.SH':'伊利股份','603259.SH':'药明康德',
    '300760.SZ':'迈瑞医疗','002475.SZ':'立讯精密','300059.SZ':'东方财富',
    '002594.SZ':'比亚迪','600276.SH':'恒瑞医药','300122.SZ':'智飞生物',
    '600030.SH':'中信证券','601888.SH':'中国中免','600585.SH':'海螺水泥',
    '601668.SH':'中国建筑','600028.SH':'中国石化','601857.SH':'中国石油',
    '600690.SH':'海尔智家','601186.SH':'中国铁建','600048.SH':'保利发展',
    '600837.SH':'海通证券','601601.SH':'新华保险','601336.SH':'新华保险',
    '002415.SZ':'海康威视','002027.SZ':'分众传媒','600009.SH':'上海机场',
    '600104.SH':'上汽集团','601766.SH':'中国中车','601989.SH':'中国重工',
    '002371.SZ':'北方华创','688981.SH':'中芯国际','603986.SH':'兆易创新',
    '688008.SH':'澜起科技','002049.SZ':'紫光国微','603501.SH':'韦尔股份',
    '688012.SH':'中微公司','002185.SZ':'华天科技','600584.SH':'长电科技',
    '300496.SZ':'中科创达','002230.SZ':'科大讯飞','300024.SZ':'机器人',
    '688256.SH':'寒武纪','688787.SH':'海天瑞声','603019.SH':'中科曙光',
    '300308.SZ':'中际旭创','002402.SZ':'和而泰','688521.SH':'唯捷创芯',
    '688297.SH':'中科星图','600893.SH':'航发动力','002025.SZ':'航天电器',
    '600118.SH':'中国卫星','300159.SZ':'新余国科','603256.SH':'宏图航天',
    '600150.SH':'中国船舶','002389.SZ':'航天彩虹','688148.SH':'航天宏图',
    '002265.SZ':'西仪股份','300015.SZ':'爱尔眼科','600196.SH':'复星医药',
    '000661.SZ':'长春高新','002007.SZ':'华兰生物','300142.SZ':'沃森生物',
    '600211.SH':'西藏药业','300347.SZ':'泰格医药','002422.SZ':'科伦药业',
    '002129.SZ':'TCL中环','600438.SH':'通威股份','002202.SZ':'金风科技',
    '300014.SZ':'亿纬锂能','603659.SH':'璞泰来','688005.SH':'容百科技',
    '000858.SZ':'五粮液','000568.SZ':'泸州老窖','600809.SH':'山西汾酒',
    '002304.SZ':'洋河股份','000596.SZ':'古井贡酒','603288.SH':'海天味业',
    '000768.SZ':'中航西飞','002013.SZ':'中航机电','600862.SH':'中航高科',
    '002238.SZ':'天融信','300115.SZ':'长盈精密',
    '300033.SZ':'同花顺','600570.SH':'恒生电子','002152.SZ':'广电运通',
    '300253.SZ':'卫宁健康',
}


# ─── 技术指标函数 ─────────────────────────────────────────
def calc_rsi_wilder(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(diff if diff > 0 else 0)
        losses.append(abs(diff) if diff < 0 else 0)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        return 100.0
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_adx(highs, lows, closes, period=14):
    if len(highs) < period * 2:
        return None
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(highs)):
        high_diff = highs[i] - highs[i - 1]
        low_diff = lows[i - 1] - lows[i]
        plus_dm.append(high_diff if high_diff > low_diff and high_diff > 0 else 0)
        minus_dm.append(low_diff if low_diff > high_diff and low_diff > 0 else 0)
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    atr = sum(trs[-period:]) / period
    plus_di = (sum(plus_dm[-period:]) / atr) * 100 if atr > 0 else 0
    minus_di = (sum(minus_dm[-period:]) / atr) * 100 if atr > 0 else 0
    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
    return dx


def calc_boll_pos(closes, period=20):
    if len(closes) < period:
        return None
    recent = closes[-period:]
    mean = statistics.mean(recent)
    std = statistics.stdev(recent)
    lower = mean - 2 * std
    upper = mean + 2 * std
    pos = (closes[-1] - lower) / (upper - lower) * 100 if (upper - lower) > 0 else 50
    return pos


def calc_obv_trend(closes, volumes):
    """计算 OBV (On-Balance Volume) 趋势方向"""
    if len(closes) < 10 or len(volumes) < 10:
        return 'flat'
    obv = 0
    obv_values = [0.0]
    for i in range(1, min(len(closes), len(volumes))):
        if closes[i] > closes[i-1]:
            obv += volumes[i]
        elif closes[i] < closes[i-1]:
            obv -= volumes[i]
        obv_values.append(float(obv))
    recent_avg = sum(obv_values[-5:]) / 5
    older_avg = sum(obv_values[-10:-5]) / 5
    if older_avg == 0:
        return 'flat'
    ratio = recent_avg / older_avg
    if ratio > 1.02:
        return 'up'
    elif ratio < 0.98:
        return 'down'
    return 'flat'


def get_tail_score(res):
    score = 0
    reasons = []
    chg = res.get('change_pct', 0)
    rsi = res.get('rsi14')
    boll = res.get('boll_pos')

    # 涨幅分级打分: 温和上涨给高分，追高给低分
    if 0.5 <= chg <= 1.5:
        score += 20
        reasons.append(f'涨幅+{chg:.2f}%(+20)')
    elif 1.5 < chg <= 3.0:
        score += 15
        reasons.append(f'涨幅+{chg:.2f}%(+15)')
    elif 3.0 < chg <= 5.0:
        score += 10
        reasons.append(f'涨幅+{chg:.2f}%(+10)')

    if rsi and 40 <= rsi <= 65:
        score += 20
        reasons.append(f'RSI={rsi:.1f}(+20)')
    if boll and 30 <= boll <= 70:
        score += 10
        reasons.append(f'BOLL={boll:.1f}%(+10)')
    if res.get('ma_gold_cross'):
        score += 10
        reasons.append('MA金叉(+10)')
    if res.get('tail_up'):
        score += 15
        reasons.append('尾盘拉升(+15)')
    if res.get('obv_trend') == 'up':
        score += 10
        reasons.append('OBV上升(+10)')
    if res.get('adx') and res['adx'] >= 25:
        score += 10
        reasons.append(f'ADX={res["adx"]:.1f}(+10)')
    if res.get('macd') is not None and res.get('macd_signal') is not None and res['macd'] > res['macd_signal']:
        score += 5
        reasons.append('MACD多头(+5)')
    return score, reasons


# ─── AmazingData 数据获取 ────────────────────────────────
def amazing_login():
    global _market, _base
    try:
        ad.login(username=AMAZING_USER, password=AMAZING_PASS,
                 host=AMAZING_HOST, port=AMAZING_PORT)
        _base = ad.BaseData()
        calendar = _base.get_calendar(data_type='str', market='SH', date=20260504)
        _market = ad.MarketData(calendar)
        print(f'  AmazingData 登录成功 (日历: {len(calendar)} 天)')
        return calendar
    except Exception as e:
        print(f'  AmazingData 登录失败: {e}')
        raise


def get_trading_dates(start_str, end_str):
    if _base is None:
        raise RuntimeError('AmazingData 未登录')
    calendar = _base.get_calendar(data_type='str', market='SH', date=20260504)
    start_int = int(start_str.replace('-', ''))
    end_int = int(end_str.replace('-', ''))
    dates = [str(d) for d in calendar if start_int <= d <= end_int]
    return [f'{d[:4]}-{d[4:6]}-{d[6:]}' for d in dates]


def fetch_daily_from_amazing(stock_code, end_date_str=None):
    if _market is None:
        raise RuntimeError('AmazingData 未登录')
    if end_date_str:
        end_dt = datetime.strptime(end_date_str, '%Y-%m-%d')
    else:
        end_dt = datetime(2099, 12, 31)
    begin_dt = end_dt - timedelta(days=365)
    end_int = int(end_dt.strftime('%Y%m%d'))
    begin_int = int(begin_dt.strftime('%Y%m%d'))
    try:
        klines = _market.query_kline(
            code_list=[stock_code],
            begin_date=begin_int,
            end_date=end_int,
            period=Period.day.value
        )
        df = klines.get(stock_code)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        df['trade_date'] = df['kline_time'].dt.strftime('%Y-%m-%d')
        df = df.sort_values('kline_time').reset_index(drop=True)
        return df[['trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount']]
    except Exception as e:
        print(f'    获取 {stock_code} 日线失败: {e}')
        return pd.DataFrame()


def fetch_minute_data(stock_code, date_str):
    if _market is None:
        raise RuntimeError('AmazingData 未登录')
    date_int = int(date_str.replace('-', ''))
    try:
        klines = _market.query_kline(
            code_list=[stock_code],
            begin_date=date_int,
            end_date=date_int,
            period=Period.min1.value
        )
        df = klines.get(stock_code)
        if df is None or df.empty:
            return None
        bars = []
        for _, row in df.iterrows():
            bars.append({
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume']),
                'amount': float(row['amount']),
                'time': row['kline_time'],
            })
        return bars
    except Exception as e:
        print(f'    获取 {stock_code} 分钟线失败: {e}')
        return None


# ─── 数据处理 ─────────────────────────────────────────────
def simulate_1430_snapshot(minute_bars, target_bar=210):
    bars = minute_bars[:target_bar]
    if not bars or len(bars) < 10:
        return None
    day_open = bars[0]['open']
    day_high = max(b['high'] for b in bars)
    day_low = min(b['low'] for b in bars)
    day_close = bars[-1]['close']
    day_volume = sum(b['volume'] for b in bars)
    day_amount = sum(b['amount'] for b in bars)
    return {
        'open': day_open, 'high': day_high, 'low': day_low,
        'close': day_close, 'volume': day_volume, 'amount': day_amount,
    }


def get_price_at_bar(minute_bars, bar_index):
    if 0 <= bar_index < len(minute_bars):
        return minute_bars[bar_index]['close']
    return None


# ─── 单只股票技术分析 (不含 Kronos) ───────────────────────
def analyze_technicals(stock_code, daily_df, minute_bars, target_bar=210):
    """只计算技术指标，不调用 Kronos"""
    snap = simulate_1430_snapshot(minute_bars, target_bar)
    if snap is None:
        return None

    if daily_df is not None and len(daily_df) > 0:
        hist_closes = daily_df['close'].tolist()
        closes = hist_closes + [snap['close']]
        opens = daily_df['open'].tolist() + [snap['open']]
        highs = daily_df['high'].tolist() + [snap['high']]
        lows = daily_df['low'].tolist() + [snap['low']]
    else:
        closes = [snap['close']]
        opens = [snap['open']]
        highs = [snap['high']]
        lows = [snap['low']]

    cur_close = snap['close']
    cur_open = snap['open']
    pre_close = daily_df['close'].iloc[-1] if daily_df is not None and len(daily_df) > 0 else cur_close
    chg_pct = (cur_close - pre_close) / pre_close * 100 if pre_close > 0 else 0

    rsi14 = calc_rsi_wilder(closes)
    boll_pos = calc_boll_pos(closes)
    adx_val = calc_adx(highs, lows, closes)

    ma5 = sum(closes[-5:]) / 5
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
    ma_gold = (ma20 is not None) and (ma5 > ma20)

    s = pd.Series(closes)
    ema12 = s.ewm(span=12, adjust=False).mean()
    ema26 = s.ewm(span=26, adjust=False).mean()
    macd_val = float(ema12.iloc[-1] - ema26.iloc[-1])
    macd_sig = float((s - s.ewm(span=9, adjust=False).mean()).ewm(span=9, adjust=False).mean().iloc[-1])

    tail_up = cur_close > cur_open * 1.005  # 收紧阈值至 0.5%

    # OBV 趋势 (使用日线 + 当日快照)
    if daily_df is not None and len(daily_df) > 0:
        daily_volumes = daily_df['volume'].tolist() + [snap['volume']]
        obv_trend = calc_obv_trend(closes, daily_volumes)
    else:
        obv_trend = 'flat'

    return {
        'stock_code': stock_code,
        'close': cur_close, 'open': cur_open, 'pre_close': pre_close,
        'change_pct': chg_pct, 'rsi14': rsi14, 'boll_pos': boll_pos,
        'ma_gold_cross': ma_gold, 'adx': adx_val,
        'macd': macd_val, 'macd_signal': macd_sig,
        'tail_up': tail_up, 'obv_trend': obv_trend,
        'kronos_pred': None,  # 后续填入
        # 保留 Kronos 所需数据
        'daily_df': daily_df,
        'minute_bars': minute_bars,
    }


def score_and_filter(res):
    """打分 & 过滤"""
    chg = res.get('change_pct', 0)
    rsi = res.get('rsi14', 50)
    boll = res.get('boll_pos', 50)

    if chg < 0.5 or chg > 9:
        return None
    if rsi and (rsi < 20 or rsi > 80):
        return None
    if boll and (boll > 85 or boll < 10):
        return None

    tail_score, reasons = get_tail_score(res)
    kronos_pred = res.get('kronos_pred')

    if kronos_pred is not None:
        composite = tail_score * TAIL_SCORE_WEIGHT + kronos_pred * KRONOS_WEIGHT
    else:
        composite = tail_score * TAIL_SCORE_WEIGHT

    if composite < MIN_COMPOSITE:
        return None

    return composite, reasons, tail_score, kronos_pred


# ─── 回测引擎 ─────────────────────────────────────────────
@dataclass
class Position:
    stock_code: str
    shares: int
    buy_price: float
    buy_date: str
    cost: float


@dataclass
class TradeLog:
    date: str
    action: str
    stock_code: str
    shares: int
    price: float
    amount: float
    pnl: float = 0.0
    reason: str = ''


@dataclass
class BacktestState:
    cash: float = INITIAL_CAPITAL
    positions: list = field(default_factory=list)
    trade_log: list = field(default_factory=list)
    daily_snapshots: list = field(default_factory=list)


def run_backtest(trading_dates):
    state = BacktestState()
    daily_api_calls = 0
    total_start = time.time()

    for day_idx, trade_date in enumerate(trading_dates):
        day_start = time.time()
        print(f'\n{"="*60}')
        print(f'交易日 [{day_idx+1}/{len(trading_dates)}]: {trade_date}')
        print(f'{"="*60}')

        # ── 1. 卖出逻辑 (延长至 14:00 平仓) ──
        sells_today = []
        positions_to_remove = []

        for pos_idx, pos in enumerate(state.positions):
            buy_date_idx = trading_dates.index(pos.buy_date)
            hold_days = day_idx - buy_date_idx
            if hold_days < HOLD_DAYS:
                continue

            print(f'  检查卖出: {pos.stock_code} (买入日={pos.buy_date}, 持仓={hold_days}天)')
            minute_bars = fetch_minute_data(pos.stock_code, trade_date)
            daily_api_calls += 1

            if minute_bars is None:
                print(f'    无分钟数据，{SELL_BAR//30+9}:00平仓')
                sell_price = pos.buy_price
                sells_today.append(TradeLog(
                    date=trade_date, action='SELL', stock_code=pos.stock_code,
                    shares=pos.shares, price=sell_price,
                    amount=pos.shares * sell_price,
                    pnl=(sell_price - pos.buy_price) * pos.shares,
                    reason='无数据平仓'
                ))
                positions_to_remove.append(pos_idx)
                continue

            # 检查 0~SELL_BAR 止盈 + 止损
            sold = False
            for bar_idx in range(min(SELL_BAR + 1, len(minute_bars))):
                current_price = minute_bars[bar_idx]['close']
                profit_pct = (current_price - pos.buy_price) / pos.buy_price * 100

                if profit_pct >= TAKE_PROFIT_PCT:
                    sell_price = current_price
                    pnl = (sell_price - pos.buy_price) * pos.shares
                    sells_today.append(TradeLog(
                        date=trade_date, action='SELL', stock_code=pos.stock_code,
                        shares=pos.shares, price=sell_price,
                        amount=pos.shares * sell_price, pnl=pnl,
                        reason=f'止盈+{profit_pct:.1f}%(bar[{bar_idx}])'
                    ))
                    positions_to_remove.append(pos_idx)
                    sold = True
                    print(f'    止盈 bar[{bar_idx}]: {profit_pct:+.1f}% @ {sell_price:.2f}')
                    break

                if profit_pct <= STOP_LOSS_PCT:
                    sell_price = current_price
                    pnl = (sell_price - pos.buy_price) * pos.shares
                    sells_today.append(TradeLog(
                        date=trade_date, action='SELL', stock_code=pos.stock_code,
                        shares=pos.shares, price=sell_price,
                        amount=pos.shares * sell_price, pnl=pnl,
                        reason=f'止损{profit_pct:+.1f}%(bar[{bar_idx}])'
                    ))
                    positions_to_remove.append(pos_idx)
                    sold = True
                    print(f'    止损 bar[{bar_idx}]: {profit_pct:+.1f}% @ {sell_price:.2f}')
                    break

            if not sold:
                # 用最后一个可用bar的价格，不超过 SELL_BAR
                max_bar = min(SELL_BAR, len(minute_bars) - 1)
                sell_price = get_price_at_bar(minute_bars, max_bar)
                if sell_price is None:
                    sell_price = pos.buy_price
                pnl = (sell_price - pos.buy_price) * pos.shares
                sells_today.append(TradeLog(
                    date=trade_date, action='SELL', stock_code=pos.stock_code,
                    shares=pos.shares, price=sell_price,
                    amount=pos.shares * sell_price, pnl=pnl,
                    reason='14:00平仓'
                ))
                positions_to_remove.append(pos_idx)
                profit_pct = (sell_price - pos.buy_price) / pos.buy_price * 100
                print(f'    14:00卖出: {profit_pct:+.1f}% @ {sell_price:.2f}')

        # 执行卖出
        for log in sells_today:
            state.trade_log.append(log)
            state.cash += log.amount
        for idx in reversed(positions_to_remove):
            state.positions.pop(idx)

        # ── 2. 选股逻辑: 分为两阶段 ──
        print(f'\n  开始选股 ({len(WATCHLIST)} 只股票)...')

        # 阶段2a: 获取数据 + 计算技术指标 (不含 Kronos)
        technicals_list = []
        api_ok = 0
        api_fail = 0

        for code in WATCHLIST:
            try:
                daily_df = _daily_cache.get(code, pd.DataFrame()).copy()
                if not daily_df.empty:
                    daily_df = daily_df[daily_df['trade_date'] < trade_date].reset_index(drop=True)

                minute_bars = fetch_minute_data(code, trade_date)
                daily_api_calls += 1

                if minute_bars is None:
                    api_fail += 1
                    continue
                api_ok += 1

                tech_res = analyze_technicals(code, daily_df, minute_bars)
                if tech_res is None:
                    continue

                technicals_list.append(tech_res)
            except Exception as e:
                print(f'    {code} 处理错误: {e}')
                continue

        # 阶段2b: 预计算 tail_score (无 Kronos)
        pre_scored = []
        for res in technicals_list:
            ts, reasons = get_tail_score(res)
            pre_scored.append((ts, res))
        pre_scored.sort(key=lambda x: x[0], reverse=True)

        print(f'  技术分析完成: {len(technicals_list)} 只, Kronos 已禁用')
        # Kronos 已移除 — 仅使用技术指标打分

        # 阶段2c: 打分 & 选股
        candidates = []
        for res in technicals_list:
            result = score_and_filter(res)
            if result is None:
                continue

            composite, reasons, tail_score, kronos_pred = result
            candidates.append({
                'code': res['stock_code'],
                'close': res['close'],
                'change_pct': res['change_pct'],
                'rsi14': res['rsi14'],
                'boll_pos': res['boll_pos'],
                'composite': composite,
                'tail_score': tail_score,
                'kronos_pred': kronos_pred,
                'reasons': reasons,
                'minute_bars': res['minute_bars'],
            })

        # 排序 & 选TOP3
        candidates.sort(key=lambda x: x['composite'], reverse=True)
        top_n = min(3, len(candidates))
        selected = candidates[:top_n]

        print(f'  API成功:{api_ok} 失败:{api_fail} | 技术符合:{len(technicals_list)} | 综合得分通过:{len(candidates)} | 选中:{top_n}')

        # ── 3. 买入逻辑 ──
        if not selected:
            print(f'  当日无符合条件的股票')
        else:
            budget_per_stock = state.cash / top_n
            buys_today = []

            for c in selected:
                name = NAME_MAP.get(c['code'], c['code'])
                buy_price = get_price_at_bar(c['minute_bars'], 210)
                if buy_price is None:
                    buy_price = c['close']

                shares = int(budget_per_stock / buy_price / 100) * 100
                if shares <= 0:
                    continue

                cost = shares * buy_price
                state.positions.append(Position(
                    stock_code=c['code'], shares=shares,
                    buy_price=buy_price, buy_date=trade_date, cost=cost
                ))
                state.cash -= cost
                buys_today.append(TradeLog(
                    date=trade_date, action='BUY', stock_code=c['code'],
                    shares=shares, price=buy_price, amount=cost,
                    reason=f'综合分{c["composite"]:.1f} | ' + ' | '.join(c['reasons'])
                ))
                print(f'  买入 {name}({c["code"]}): {shares}股 @ {buy_price:.2f} = {cost:.0f} (综合分:{c["composite"]:.1f})')

            for log in buys_today:
                state.trade_log.append(log)

        # ── 4. 当日持仓快照 ──
        total_position_value = sum(p.shares * p.buy_price for p in state.positions)
        state.daily_snapshots.append({
            'date': trade_date,
            'cash': state.cash,
            'positions': len(state.positions),
            'total_value': state.cash + total_position_value,
        })

        day_elapsed = time.time() - day_start
        print(f'  现金: {state.cash:,.0f} | 持仓: {len(state.positions)} | 当日耗时: {day_elapsed:.0f}s')

        # 每 5 天保存一次检查点
        if (day_idx + 1) % 5 == 0:
            ckpt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f'backtest_ckpt_day{day_idx+1}.json')
            ckpt = {
                'day_idx': day_idx, 'trade_date': trade_date,
                'cash': state.cash,
                'positions': [(p.stock_code, p.shares, p.buy_price, p.buy_date, p.cost) for p in state.positions],
                'trade_log': [(t.date, t.action, t.stock_code, t.shares, t.price, t.amount, t.pnl, t.reason) for t in state.trade_log],
                'daily_snapshots': state.daily_snapshots,
            }
            with open(ckpt_path, 'w') as f:
                json.dump(ckpt, f, ensure_ascii=False)
            print(f'  检查点已保存: {ckpt_path}')

    # 回测结束清仓
    print(f'\n{"="*60}')
    print(f'回测结束，强制清仓 {len(state.positions)} 个持仓...')
    if state.positions:
        last_date = trading_dates[-1]
        for pos in state.positions:
            sell_price = pos.buy_price
            sell_amount = pos.shares * sell_price
            pnl = (sell_price - pos.buy_price) * pos.shares
            state.trade_log.append(TradeLog(
                date=last_date, action='SELL', stock_code=pos.stock_code,
                shares=pos.shares, price=sell_price,
                amount=sell_amount, pnl=pnl, reason='回测结束清仓'
            ))
            state.cash += sell_amount
        state.positions.clear()

    total_elapsed = time.time() - total_start
    print(f'总耗时: {total_elapsed:.0f}s ({total_elapsed/60:.1f}分钟)')
    print(f'总API调用: {daily_api_calls}')
    return state


# ─── 报告生成 ─────────────────────────────────────────────
def generate_report(state, trading_dates):
    print(f'\n\n{"="*70}')
    print(f'                    回测报告 (v4)')
    print(f'{"="*70}')
    print(f'回测区间: {trading_dates[0]} ~ {trading_dates[-1]}')
    print(f'初始资金: {INITIAL_CAPITAL:,.0f}')
    print(f'止盈: +{TAKE_PROFIT_PCT:.0f}% | 止损: {STOP_LOSS_PCT:+.0f}% | 最低分: {MIN_COMPOSITE:.0f} | 持仓: T+{HOLD_DAYS} | 平仓: 14:00')
    print(f'最终现金: {state.cash:,.0f}')
    print(f'收益率: {(state.cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100:+.2f}%')
    print()

    buys = [t for t in state.trade_log if t.action == 'BUY']
    sells = [t for t in state.trade_log if t.action == 'SELL']

    DASH = '─' * 70

    print(f'{DASH}')
    print(f'买入记录 ({len(buys)} 笔):')
    print(f'{DASH}')
    print(f'{"日期":<12} {"股票":<14} {"数量":>6} {"价格":>8} {"金额":>10} {"原因"}')
    print(f'{"-"*100}')
    for t in buys:
        name = NAME_MAP.get(t.stock_code, t.stock_code)
        print(f'{t.date:<12} {name}({t.stock_code[-6:]}) {t.shares:>6} {t.price:>8.2f} {t.amount:>10,.0f} {t.reason}')

    print()
    print(f'{DASH}')
    print(f'卖出记录 ({len(sells)} 笔):')
    print(f'{DASH}')
    print(f'{"日期":<12} {"股票":<14} {"数量":>6} {"价格":>8} {"金额":>10} {"盈亏":>8} {"原因"}')
    print(f'{"-"*110}')
    for t in sells:
        name = NAME_MAP.get(t.stock_code, t.stock_code)
        pnl_str = f'{t.pnl:+,.0f}'
        print(f'{t.date:<12} {name}({t.stock_code[-6:]}) {t.shares:>6} {t.price:>8.2f} {t.amount:>10,.0f} {pnl_str:>8} {t.reason}')

    total_pnl = sum(t.pnl for t in sells)
    wins = [t for t in sells if t.pnl > 0]
    losses = [t for t in sells if t.pnl <= 0]

    print()
    print(f'{DASH}')
    print(f'交易统计:')
    print(f'{DASH}')
    print(f'  总买入: {len(buys)} 笔')
    print(f'  总卖出: {len(sells)} 笔')
    print(f'  总盈亏: {total_pnl:+,.0f}')
    print(f'  胜率: {len(wins)/len(sells)*100:.1f}%' if sells else '  胜率: N/A')
    if wins:
        avg_win = sum(t.pnl for t in wins) / len(wins)
        print(f'  平均盈利: +{avg_win:,.0f}')
    if losses:
        avg_loss = sum(t.pnl for t in losses) / len(losses)
        print(f'  平均亏损: {avg_loss:+,.0f}')
    if wins and losses:
        avg_win = sum(t.pnl for t in wins) / len(wins)
        avg_loss = abs(sum(t.pnl for t in losses) / len(losses))
        if avg_loss > 0:
            print(f'  盈亏比: {avg_win/avg_loss:.2f}')

    print()
    print(f'{DASH}')
    print(f'每日净值:')
    print(f'{DASH}')
    print(f'{"日期":<12} {"现金":>12} {"持仓数":>6} {"总资产":>12} {"收益率":>8}')
    print(f'{"-"*60}')
    for s in state.daily_snapshots:
        ret = (s['total_value'] - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
        print(f'{s["date"]:<12} {s["cash"]:>12,.0f} {s["positions"]:>6} {s["total_value"]:>12,.0f} {ret:>+7.2f}%')

    # 保存JSON报告
    report = {
        'start_date': trading_dates[0], 'end_date': trading_dates[-1],
        'initial_capital': INITIAL_CAPITAL, 'final_cash': state.cash,
        'return_pct': (state.cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100,
        'total_trades': len(state.trade_log), 'buys': len(buys), 'sells': len(sells),
        'wins': len(wins), 'losses': len(losses),
        'win_rate': len(wins) / len(sells) * 100 if sells else 0,
        'total_pnl': total_pnl,
        'avg_win': sum(t.pnl for t in wins) / len(wins) if wins else 0,
        'avg_loss': sum(t.pnl for t in losses) / len(losses) if losses else 0,
        'trades': [
            {'date': t.date, 'action': t.action, 'stock_code': t.stock_code,
             'shares': t.shares, 'price': t.price, 'amount': t.amount,
             'pnl': t.pnl, 'reason': t.reason}
            for t in state.trade_log
        ],
        'daily_snapshots': state.daily_snapshots,
    }

    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backtest_report_v4.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f'\n报告已保存: {report_path}')


# ─── 主入口 ───────────────────────────────────────────────
if __name__ == '__main__':
    print('尾盘选股策略回测 v4 (优化版)')
    print(f'回测区间: 2026-04-01 ~ 2026-04-30')
    print(f'初始资金: {INITIAL_CAPITAL:,.0f}')
    print(f'Watchlist: {len(WATCHLIST)} 只股票')
    print(f'止盈: +{TAKE_PROFIT_PCT:.0f}% | 止损: {STOP_LOSS_PCT:+.0f}% | 最低分: {MIN_COMPOSITE:.0f}')
    print(f'持仓: T+{HOLD_DAYS} | 平仓: 14:00 | tail_score: {TAIL_SCORE_WEIGHT} | kronos: {KRONOS_WEIGHT}')

    print('\n登录 AmazingData...')
    amazing_login()

    print('\n获取交易日列表...')
    trading_dates = get_trading_dates('2026-04-01', '2026-04-30')
    print(f'共 {len(trading_dates)} 个交易日: {trading_dates[:5]}...{trading_dates[-3:]}')

    print(f'\n预取 {len(WATCHLIST)} 只股票日线数据...')
    for i, code in enumerate(WATCHLIST):
        if (i + 1) % 20 == 0:
            print(f'  进度: {i+1}/{len(WATCHLIST)}')
        _daily_cache[code] = fetch_daily_from_amazing(code, end_date_str='2026-04-30')
    print(f'日线预取完成 ({len(_daily_cache)} 只)')

    if not trading_dates:
        print('错误: 未找到交易日数据')
        sys.exit(1)

    seen = set()
    unique_watchlist = []
    for c in WATCHLIST:
        if c not in seen:
            seen.add(c)
            unique_watchlist.append(c)
    globals()['WATCHLIST'] = unique_watchlist
    print(f'股票池: {len(WATCHLIST)} 只 (已去重)')

    state = run_backtest(trading_dates)
    generate_report(state, trading_dates)
