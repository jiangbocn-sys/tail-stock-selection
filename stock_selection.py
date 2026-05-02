"""
尾盘选股策略 v5 - Tail Session Stock Selection Strategy

交易周期：T+0短线，当日14:30买入，次日9:30-10:00卖出
核心逻辑：技术面打分 + Kronos趋势过滤
"""

import akshare as ak
import pandas as pd
import numpy as np
from typing import Optional
import warnings

warnings.filterwarnings('ignore', category=FutureWarning)

# ============================================================================
# 一、动态股票池构建
# ============================================================================

def get_hs300_stocks() -> list[str]:
    """获取沪深300成分股"""
    try:
        df = ak.index_stock_cons_weight_csindex(symbol='000300')
        return df['成分券代码'].tolist()
    except Exception as e:
        print(f"获取沪深300成分股失败: {e}")
        return []


def calc_sector_heat() -> tuple[list[str], pd.DataFrame]:
    """
    计算板块热度排名
    热度 = 涨幅标准化×0.5 + 资金净流入标准化×0.5

    返回：(热度前5的板块名称列表, 完整热度数据DataFrame)
    """
    # 1. 获取板块涨幅数据
    change_df = ak.stock_board_industry_index_em()
    change_df = change_df[['板块名称', '涨跌幅']].copy()
    change_df['涨跌幅'] = pd.to_numeric(change_df['涨跌幅'], errors='coerce')

    # 2. 获取板块资金流向数据
    flow_df = ak.stock_board_industry_fund_flow_em()
    flow_df = flow_df[['名称', '主力净流入']].copy()
    flow_df.columns = ['板块名称', '主力净流入']
    flow_df['主力净流入'] = pd.to_numeric(flow_df['主力净流入'], errors='coerce')

    # 3. 标准化处理（Min-Max归一化到0-100分）
    def normalize(series: pd.Series) -> pd.Series:
        min_val = series.min()
        max_val = series.max()
        if max_val == min_val:
            return pd.Series([50] * len(series), index=series.index)
        return ((series - min_val) / (max_val - min_val) * 100).round(2)

    change_df['change_norm'] = normalize(change_df['涨跌幅'])
    flow_df['flow_norm'] = normalize(flow_df['主力净流入'])

    # 4. 合并计算热度得分
    heat_df = change_df.merge(flow_df, on='板块名称', how='inner')
    heat_df['heat_score'] = (
        heat_df['change_norm'] * 0.5 +
        heat_df['flow_norm'] * 0.5
    ).round(2)

    # 5. 返回热度前5的板块
    heat_df = heat_df.sort_values('heat_score', ascending=False)
    top5_sectors = heat_df.head(5)['板块名称'].tolist()

    return top5_sectors, heat_df


def get_hot_stocks(
    hot_sectors: list[str],
    min_market_cap: float = 30e8,
    max_change_pct: float = 9
) -> list[str]:
    """
    获取热门板块成分股

    参数：
    - hot_sectors: 热门板块名称列表
    - min_market_cap: 最小流通市值（默认30亿）
    - max_change_pct: 最大涨幅（默认9%，过滤接近涨停）

    返回：符合条件的股票代码列表
    """
    stocks = []

    for sector in hot_sectors:
        try:
            cons_df = ak.stock_board_industry_cons_em(symbol=sector)

            cons_df['流通市值'] = pd.to_numeric(cons_df['流通市值'], errors='coerce')
            cons_df['涨跌幅'] = pd.to_numeric(cons_df['涨跌幅'], errors='coerce')

            filtered = cons_df[
                (cons_df['流通市值'] >= min_market_cap) &
                (cons_df['涨跌幅'] < max_change_pct) &
                (cons_df['涨跌幅'] > -10)
            ]

            # 每个板块取涨幅前10名
            filtered = filtered.nlargest(10, '涨跌幅')
            stocks.extend(filtered['代码'].tolist())

        except Exception as e:
            print(f"获取板块 {sector} 成分股失败: {e}")
            continue

    return list(set(stocks))


def build_daily_stock_pool() -> tuple[list[str], list[str]]:
    """
    构建当日股票池

    返回：(去重后的股票代码列表, 热门板块名称列表)
    """
    hs300_stocks = get_hs300_stocks()
    hot_sectors, _ = calc_sector_heat()
    hot_stocks = get_hot_stocks(hot_sectors)

    final_pool = list(set(hs300_stocks + hot_stocks))

    print(f"股票池构建完成：沪深300 {len(hs300_stocks)}只 + 热门板块 {len(hot_stocks)}只 = 去重后 {len(final_pool)}只")
    print(f"热门板块：{hot_sectors}")

    return final_pool, hot_sectors


# ============================================================================
# 二、数据获取与处理
# ============================================================================

def calc_technical_indicators(df: pd.DataFrame, current_price: float) -> dict:
    """
    计算技术指标

    df: 历史K线数据（至少30日），需包含 close/high/low 列
    current_price: 当前价格（14:30时点）
    """
    import talib

    close = df['close'].values.astype(float)

    # RSI
    rsi = talib.RSI(close, timeperiod=14)[-1]

    # 布林带
    upper, middle, lower = talib.BBANDS(close, timeperiod=20)
    upper, lower = upper[-1], lower[-1]
    boll_position = (current_price - lower) / (upper - lower) * 100 if upper != lower else 50

    # MA金叉
    ma5 = talib.MA(close, timeperiod=5)[-1]
    ma20 = talib.MA(close, timeperiod=20)[-1]
    ma_cross = bool(ma5 > ma20)

    # ADX
    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)
    adx = talib.ADX(high, low, close, timeperiod=14)[-1]

    # MACD
    macd, macdsignal, _ = talib.MACD(close)
    macd_bull = bool(macd[-1] > macdsignal[-1])

    return {
        'rsi': round(float(rsi), 2),
        'boll_position': round(float(boll_position), 2),
        'ma_cross': ma_cross,
        'ma5': round(float(ma5), 2),
        'ma20': round(float(ma20), 2),
        'adx': round(float(adx), 2),
        'macd_bull': macd_bull
    }


def fetch_stock_data(code: str) -> Optional[dict]:
    """
    获取单只股票的完整数据

    返回：包含行情、技术指标等完整数据的字典，失败返回None
    """
    try:
        # 1. 获取实时行情
        spot_df = ak.stock_zh_a_spot_em()
        spot_row = spot_df[spot_df['代码'] == code]
        if spot_row.empty:
            return None

        row = spot_row.iloc[0]

        # 2. 获取历史K线（用于技术指标）
        hist_df = ak.stock_zh_a_hist(
            symbol=code,
            period='daily',
            start_date='20250101',
            end_date=pd.Timestamp.today().strftime('%Y%m%d')
        )
        if len(hist_df) < 30:
            return None

        hist_df.columns = ['date', 'open', 'close', 'high', 'low', 'volume',
                           'turnover', 'amplitude', 'change_pct', 'change', 'turnover_rate']

        # 3. 获取分时数据（尾盘分析）
        try:
            intraday_df = ak.stock_intraday_em(symbol=code)
            # 筛选14:00-14:30的数据
            if '时间' in intraday_df.columns:
                mask = intraday_df['时间'].str.contains('14:0[0-9]|14:1[0-9]|14:2[0-9]|14:30', na=False)
            else:
                mask = pd.Series([False] * len(intraday_df))
            tail_df = intraday_df[mask]
            prices_14_14_30 = tail_df['价格'].tolist() if not tail_df.empty and '价格' in tail_df.columns else []
            volume_14_14_30 = tail_df['成交量'].sum() if not tail_df.empty and '成交量' in tail_df.columns else 0
        except Exception:
            prices_14_14_30 = []
            volume_14_14_30 = 0

        # 4. 计算技术指标
        current_price = float(row['最新价'])
        indicators = calc_technical_indicators(hist_df, current_price)

        # 5. 计算上市天数
        first_date = pd.to_datetime(hist_df['date'].iloc[0])
        list_days = (pd.Timestamp.today() - first_date).days

        # 6. 计算尾盘量比
        total_volume = float(row['成交量']) if '成交量' in row.index else 0
        if prices_14_14_30 and volume_14_14_30 > 0:
            volume_ratio = calc_tail_volume_ratio(
                volume_14_14_30, total_volume
            )
        else:
            volume_ratio = 1.0

        return {
            'code': code,
            'name': str(row['名称']),
            'change_pct': float(row['涨跌幅']),
            'current_price': current_price,
            'float_mv': float(row['流通市值']),
            'total_volume': total_volume,
            'list_days': list_days,
            'prices_14_14_30': prices_14_14_30,
            'volume_14_14_30': volume_14_14_30,
            'volume_ratio': volume_ratio,
            **indicators
        }

    except Exception as e:
        print(f"获取 {code} 数据失败: {e}")
        return None


from kronos_integration import fetch_kronos_predictions


def fetch_all_data(stock_pool: list[str]) -> list[dict]:
    """获取股票池中所有股票的数据"""
    print("  正在获取股票数据和Kronos预测...")

    results = []
    for i, code in enumerate(stock_pool):
        if i % 50 == 0:
            print(f"    进度：{i}/{len(stock_pool)}")
        data = fetch_stock_data(code)
        if data is not None:
            results.append(data)

    # 获取Kronos预测
    codes = [d['code'] for d in results]
    kronos_preds = fetch_kronos_predictions(codes)
    for d in results:
        d['kronos_prediction'] = kronos_preds.get(d['code'], 0.0)

    print(f"  数据获取完成：{len(results)}/{len(stock_pool)}只")
    return results


# ============================================================================
# 三、硬过滤条件（第一层过滤）
# ============================================================================

def hard_filter(stock_data: dict) -> tuple[bool, Optional[str]]:
    """
    硬过滤条件

    返回: (是否通过, 剔除原因)
    """
    # F1: 涨幅过滤
    if stock_data['change_pct'] < 0.5:
        return False, f"涨幅{stock_data['change_pct']:.2f}% < 0.5%，太弱"
    if stock_data['change_pct'] > 9:
        return False, f"涨幅{stock_data['change_pct']:.2f}% > 9%，接近涨停"

    # F2: RSI过滤
    if stock_data['rsi'] < 20:
        return False, f"RSI={stock_data['rsi']:.1f} < 20，极端超卖"
    if stock_data['rsi'] > 80:
        return False, f"RSI={stock_data['rsi']:.1f} > 80，极端超买"

    # F3: BOLL位置过滤
    if stock_data['boll_position'] < 10:
        return False, f"BOLL位置{stock_data['boll_position']:.1f}% < 10%，极端低估"
    if stock_data['boll_position'] > 85:
        return False, f"BOLL位置{stock_data['boll_position']:.1f}% > 85%，极端高估"

    # F4: 流通市值过滤
    if stock_data['float_mv'] < 30e8:
        return False, f"流通市值{stock_data['float_mv']/1e8:.1f}亿 < 30亿"

    # F5: ST股过滤
    if 'ST' in stock_data['name']:
        return False, "ST股不纳入"

    # F6: 次新股过滤
    if stock_data['list_days'] < 60:
        return False, f"上市{stock_data['list_days']}天 < 60天，次新股"

    # F7: 涨跌停过滤
    if stock_data['change_pct'] >= 9.8:
        return False, f"涨幅{stock_data['change_pct']:.2f}%，涨停无法买入"
    if stock_data['change_pct'] <= -9.8:
        return False, f"跌幅{stock_data['change_pct']:.2f}%，跌停风险大"

    return True, None


# ============================================================================
# 四、Kronos趋势过滤（第二层过滤）
# ============================================================================

def kronos_filter(kronos_prediction: float) -> tuple[bool, str]:
    """
    Kronos趋势过滤

    kronos_prediction: Kronos预测的5日平均涨跌幅(%)

    返回: (是否通过, 说明)
    """
    if kronos_prediction > 0:
        return True, f"预测+{kronos_prediction:.2f}%，趋势向上"
    elif kronos_prediction == 0:
        return False, "预测0%，趋势不明"
    else:
        return False, f"预测{kronos_prediction:.2f}%，趋势向下"


# ============================================================================
# 五、技术面打分体系（满分100分）
# ============================================================================

def check_tail_rally(prices_14_14_30: list[float]) -> bool:
    """
    判断尾盘拉升

    prices_14_14_30: 14:00-14:30的价格序列

    返回: True表示尾盘拉升，得15分；False不得分
    """
    if len(prices_14_14_30) < 5:
        return False

    start_price = prices_14_14_30[0]
    end_price = prices_14_14_30[-1]
    max_price = max(prices_14_14_30)

    # 条件1: 涨幅>0.5%
    if end_price <= start_price * 1.005:
        return False

    # 条件2: 最高点出现在后半段（更接近14:30）
    mid_idx = len(prices_14_14_30) // 2
    second_half_max = max(prices_14_14_30[mid_idx:])

    if second_half_max >= max_price * 0.998:
        return True

    return False


def calc_tail_volume_ratio(
    volume_14_14_30: float,
    total_volume: float,
    trading_minutes: int = 240
) -> float:
    """
    计算尾盘量比

    尾盘30分钟均量 / 全天均量
    """
    if total_volume == 0 or trading_minutes == 0:
        return 1.0

    tail_avg = volume_14_14_30 / 30
    daily_avg = total_volume / trading_minutes

    return tail_avg / daily_avg if daily_avg != 0 else 1.0


def calc_technical_score(stock_data: dict) -> tuple[int, list[str]]:
    """
    计算技术面得分

    返回: (总分, 各项得分详情)
    """
    score = 0
    details = []

    # T1: 当日涨幅
    if 1 <= stock_data['change_pct'] <= 5:
        score += 20
        details.append(f"T1涨幅+20分：{stock_data['change_pct']:.2f}%在1%-5%区间")
    else:
        details.append(f"T1涨幅+0分：{stock_data['change_pct']:.2f}%不在1%-5%区间")

    # T2: RSI
    if 40 <= stock_data['rsi'] <= 65:
        score += 20
        details.append(f"T2 RSI+20分：{stock_data['rsi']:.1f}在40-65区间")
    else:
        details.append(f"T2 RSI+0分：{stock_data['rsi']:.1f}不在40-65区间")

    # T3: BOLL位置
    if 30 <= stock_data['boll_position'] <= 70:
        score += 10
        details.append(f"T3 BOLL+10分：{stock_data['boll_position']:.1f}%在30%-70%区间")
    else:
        details.append(f"T3 BOLL+0分：{stock_data['boll_position']:.1f}%不在30%-70%区间")

    # T4: MA金叉
    if stock_data['ma_cross']:
        score += 10
        details.append(f"T4 MA金叉+10分：MA5({stock_data['ma5']:.2f}) > MA20({stock_data['ma20']:.2f})")
    else:
        details.append(f"T4 MA金叉+0分：MA5({stock_data['ma5']:.2f}) <= MA20({stock_data['ma20']:.2f})")

    # T5: 尾盘拉升
    if check_tail_rally(stock_data['prices_14_14_30']):
        score += 15
        details.append("T5尾盘拉升+15分：14:00后价格持续向上")
    else:
        details.append("T5尾盘拉升+0分：未检测到尾盘拉升")

    # T6: 尾盘放量
    if stock_data.get('volume_ratio', 1.0) > 1.2:
        score += 10
        details.append(f"T6尾盘放量+10分：量比{stock_data['volume_ratio']:.2f} > 1.2")
    else:
        details.append(f"T6尾盘放量+0分：量比{stock_data.get('volume_ratio', 1.0):.2f} <= 1.2")

    # T7: ADX
    if stock_data['adx'] >= 25:
        score += 10
        details.append(f"T7 ADX+10分：{stock_data['adx']:.1f} >= 25")
    else:
        details.append(f"T7 ADX+0分：{stock_data['adx']:.1f} < 25")

    # T8: MACD多头
    if stock_data['macd_bull']:
        score += 5
        details.append("T8 MACD+5分：MACD > Signal")
    else:
        details.append("T8 MACD+0分：MACD <= Signal")

    return score, details


# ============================================================================
# 六、大盘情绪调整
# ============================================================================

def adjust_by_market(score: int, market_change_pct: float) -> tuple[int, float, str]:
    """
    根据大盘表现调整操作建议

    score: 技术面得分
    market_change_pct: 大盘涨跌幅(%)

    返回: (调整后门槛, 仓位调整系数, 情绪说明)
    """
    if market_change_pct > 1:
        return 55, 1.2, "大盘偏多，门槛不变，仓位可+20%"
    elif market_change_pct >= -1:
        return 55, 1.0, "大盘震荡，标准操作"
    else:
        return 65, 0.5, "大盘偏空，门槛+10分，仓位减半或空仓"


def get_action_recommendation(score: int, market_change_pct: float) -> tuple[str, str]:
    """
    根据得分和大盘情绪获取操作建议

    返回: (操作建议, 仓位建议)
    """
    threshold, position_coeff, sentiment = adjust_by_market(score, market_change_pct)

    if score >= 70 and score >= threshold:
        return "强烈推荐", f"{int(15*position_coeff)}-{int(20*position_coeff)}%"
    elif score >= 55 and score >= threshold:
        return "适合建仓", f"{int(10*position_coeff)}-{int(15*position_coeff)}%"
    elif score >= 40 and score >= threshold:
        return "谨慎观望", f"{int(5*position_coeff)}-{int(8*position_coeff)}%"
    else:
        return "不建议", "不操作"


# ============================================================================
# 七、完整筛选流程
# ============================================================================

def run_daily_selection(
    date_str: Optional[str] = None,
    market_change_pct: float = 0.0
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    执行当日选股流程

    参数：
    - date_str: 日期字符串，如 '2026-05-02'
    - market_change_pct: 大盘当日涨跌幅(%)

    返回: (建仓候选名单, 硬过滤剔除名单, 趋势过滤剔除名单)
    """
    if date_str is None:
        date_str = pd.Timestamp.today().strftime('%Y-%m-%d')

    print(f"【{date_str}】开始选股流程...")

    # ========== 第一步：构建股票池 ==========
    print("【第一步】构建股票池...")
    stock_pool, hot_sectors = build_daily_stock_pool()
    total_count = len(stock_pool)
    print(f"股票池构建完成：{total_count}只")

    # ========== 第二步：获取数据 ==========
    print("【第二步】获取数据...")
    stock_data_list = fetch_all_data(stock_pool)

    # ========== 第三步：硬过滤 ==========
    print("【第三步】硬过滤...")
    passed_hard_filter = []
    hard_filtered = []

    for stock in stock_data_list:
        passed, reason = hard_filter(stock)
        if passed:
            passed_hard_filter.append(stock)
        else:
            hard_filtered.append({
                'code': stock['code'],
                'name': stock['name'],
                'reason': reason
            })

    print(f"硬过滤：通过{len(passed_hard_filter)}只，剔除{len(hard_filtered)}只")

    # ========== 第四步：Kronos趋势过滤 ==========
    print("【第四步】Kronos趋势过滤...")
    passed_kronos = []
    kronos_filtered = []

    for stock in passed_hard_filter:
        passed, reason = kronos_filter(stock['kronos_prediction'])
        if passed:
            passed_kronos.append(stock)
        else:
            kronos_filtered.append({
                'code': stock['code'],
                'name': stock['name'],
                'kronos_prediction': stock['kronos_prediction'],
                'reason': reason
            })

    print(f"趋势过滤：通过{len(passed_kronos)}只，剔除{len(kronos_filtered)}只")

    # ========== 第五步：技术面打分 ==========
    print("【第五步】技术面打分...")
    scored_stocks = []

    for stock in passed_kronos:
        score, details = calc_technical_score(stock)
        action, position = get_action_recommendation(score, market_change_pct)
        scored_stocks.append({
            'code': stock['code'],
            'name': stock['name'],
            'change_pct': stock['change_pct'],
            'rsi': stock['rsi'],
            'boll_position': stock['boll_position'],
            'score': score,
            'kronos_prediction': stock['kronos_prediction'],
            'action': action,
            'position': position,
            'details': details
        })

    # 按得分降序排序
    scored_stocks.sort(key=lambda x: x['score'], reverse=True)

    # ========== 第六步：生成统计汇总 ==========
    print_summary(date_str, total_count, len(stock_data_list),
                  len(hard_filtered), len(kronos_filtered), scored_stocks)

    return scored_stocks, hard_filtered, kronos_filtered


def print_summary(
    date_str: str,
    pool_count: int,
    data_count: int,
    hard_filtered_count: int,
    kronos_filtered_count: int,
    candidates: list[dict]
) -> None:
    """打印统计汇总"""
    print("\n" + "=" * 80)
    print(f"【选股结果】{date_str} 14:30")
    print("=" * 80)

    # 建仓候选
    actionable = [c for c in candidates if c['action'] != '不建议']
    if actionable:
        print("\n【建仓候选】")
        print(f"{'排名':<4} {'代码':<8} {'名称':<10} {'涨幅':<8} {'RSI':<6} {'BOLL位':<7} {'技术分':<6} {'Kronos':<8} {'操作建议':<10} {'仓位':<8}")
        print("-" * 95)
        for i, stock in enumerate(actionable, 1):
            print(f"{i:<4} {stock['code']:<8} {stock['name']:<10} "
                  f"{stock['change_pct']:+.1f}%{'':<3} {stock['rsi']:<6.0f} "
                  f"{stock['boll_position']:.0f}%{'':<3} {stock['score']:<6} "
                  f"{stock['kronos_prediction']:+.1f}%{'':<3} "
                  f"{stock['action']:<10} {stock['position']:<8}")
    else:
        print("\n【建仓候选】无符合条件的股票")

    # 统计汇总
    print(f"\n【统计汇总】")
    print(f"股票池总数：{pool_count}只")
    print(f"数据获取成功：{data_count}只")
    print(f"硬过滤通过：{data_count - hard_filtered_count}只（剔除{hard_filtered_count}只）")
    print(f"趋势过滤通过：{data_count - hard_filtered_count - kronos_filtered_count}只（剔除{kronos_filtered_count}只）")
    print(f"最终候选：{len(actionable)}只")

    # 得分分布
    score_ranges = [
        (70, 100, "≥70分", "强烈推荐"),
        (55, 69, "55-69分", "适合建仓"),
        (40, 54, "40-54分", "谨慎观望"),
        (0, 39, "<40分", "不建议")
    ]

    print(f"\n得分分布：")
    for low, high, label, action_label in score_ranges:
        count = len([c for c in candidates if low <= c['score'] <= high])
        print(f"  - {label}：{count}只（{action_label}）")


# ============================================================================
# 入口
# ============================================================================

if __name__ == '__main__':
    print("尾盘选股策略 v5")
    print("=" * 40)

    # 执行选股
    candidates, hard_filtered, kronos_filtered = run_daily_selection(
        date_str='2026-05-02',
        market_change_pct=0.0  # 可根据实际大盘情况调整
    )
