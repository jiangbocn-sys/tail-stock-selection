"""
Kronos 预测集成模块 - 用于尾盘选股策略

使用系统中已安装的 Kronos 模型进行股票趋势预测。
模型路径: /Users/bobo/.openclaw/workspace/Kronos/

原则：不使用模拟数据，数据不可用则如实报告失败。
"""

import sys
import os
import subprocess
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional

# Kronos 系统路径（自动检测环境）
import platform
_is_macos = platform.system() == "Darwin"
KRONOS_PATH = "/Users/bobo/.openclaw/workspace/Kronos" if _is_macos else os.path.expanduser("~/workspace/Kronos")
OUTPUT_DIR = "/Users/bobo/.openclaw/workspace/stock_data" if _is_macos else os.path.expanduser("~/workspace/stock_data")
sys.path.insert(0, KRONOS_PATH)

# 模型路径
MODEL_PATH = os.path.join(KRONOS_PATH, "models/Kronos-base")
TOKENIZER_PATH = os.path.join(KRONOS_PATH, "models/Kronos-Tokenizer-base")


# ============================================================================
# 数据获取（仅使用真实数据源）
# ============================================================================

def get_stock_prefix(code: str) -> str:
    """根据股票代码获取前缀（sh/sz）"""
    # 兼容 "000001.SZ" 和 "000001" 两种格式
    code = code.split('.')[0]
    if code.startswith('6'):
        return 'sh'
    elif code.startswith(('0', '3')):
        return 'sz'
    return 'sh'


def fetch_kline_from_10jqka(code: str) -> Optional[pd.DataFrame]:
    """
    从同花顺HTTP API获取历史K线数据

    返回：包含 date/open/high/low/close/volume/amount 的DataFrame
    """
    prefix = get_stock_prefix(code)

    try:
        cmd = (
            f'curl -s -H "User-Agent: Mozilla/5.0" '
            f'-H "Referer: http://stockpage.10jqka.com.cn/" '
            f'"http://d.10jqka.com.cn/v2/line/hs_{prefix}{code}/01/all.js"'
        )
        result = subprocess.run(cmd, shell=True, capture_output=True, timeout=15)

        if result.returncode != 0 or not result.stdout:
            print(f"    [Kronos] ❌ {code} 同花顺API无响应")
            return None

        text = result.stdout.decode('utf-8', errors='ignore')

        if '"' not in text:
            print(f"    [Kronos] ❌ {code} 同花顺API返回格式异常")
            return None

        data_part = text.split('"')[1]
        lines = data_part.strip().rstrip(';').split(';')

        records = []
        for line in lines:
            parts = line.split(',')
            if len(parts) >= 7:
                records.append({
                    'date': parts[0],
                    'open': float(parts[1]),
                    'high': float(parts[2]),
                    'low': float(parts[3]),
                    'close': float(parts[4]),
                    'volume': float(parts[5]),
                    'amount': float(parts[6])
                })

        if records:
            df = pd.DataFrame(records)
            df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
            return df

        print(f"    [Kronos] ❌ {code} 同花顺API返回空数据")
        return None

    except subprocess.TimeoutExpired:
        print(f"    [Kronos] ❌ {code} 同花顺API超时")
        return None
    except Exception as e:
        print(f"    [Kronos] ❌ {code} 同花顺API异常: {e}")
        return None


def fetch_kline_from_stockwinner(code: str) -> Optional[pd.DataFrame]:
    """
    从 StockWinner 本地 SQLite 数据库查询 K 线数据
    使用 database query endpoint，比 /market/kline 更可靠
    """
    try:
        if '.' not in code:
            prefix = get_stock_prefix(code)
            stock_code = f"{code}.{prefix.upper()}"
        else:
            stock_code = code

        import requests
        resp = requests.post(
            f"http://localhost:8080/api/v1/ui/databases/kline/tables/kline_data/query",
            json={"query": f'SELECT * FROM kline_data WHERE stock_code = "{stock_code}" ORDER BY trade_date DESC LIMIT 500'},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()

        rows = data.get("data", [])
        if not rows or len(rows) < 30:
            return None

        records = []
        for k in rows:
            records.append({
                'date': k.get('trade_date', ''),
                'open': float(k.get('open', 0)),
                'high': float(k.get('high', 0)),
                'low': float(k.get('low', 0)),
                'close': float(k.get('close', 0)),
                'volume': float(k.get('volume', 0)),
                'amount': float(k.get('amount', 0)),
            })

        df = pd.DataFrame(records)
        df['date'] = pd.to_datetime(df['date'])
        print(f"    [Kronos] DB {code} StockWinner: {len(df)}天")
        return df

    except Exception as e:
        print(f"    [Kronos] ❌ {code} StockWinner DB查询失败: {e}")
        return None


def fetch_kline_data(code: str, lookback: int = 400) -> Optional[pd.DataFrame]:
    """
    获取股票历史K线数据

    优先级：本地CSV缓存 > StockWinner API > 同花顺HTTP API
    无可用数据源时返回 None，绝不使用模拟数据。
    """
    # 1. 优先使用本地CSV缓存
    csv_path = os.path.join(OUTPUT_DIR, f"{code}_kline.csv")
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path)
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').tail(lookback)
            if 'amount' not in df.columns:
                df['amount'] = df['volume'] * df['close'] * 100
            print(f"    [Kronos] 📁 {code} 本地CSV: {len(df)}天")
            return df
        except Exception as e:
            print(f"    [Kronos] ❌ {code} 本地CSV读取失败: {e}")

    # 2. StockWinner 本地 API
    df = fetch_kline_from_stockwinner(code)
    if df is not None and len(df) >= 30:
        df = df.sort_values('date').tail(lookback)
        try:
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            df.to_csv(csv_path, index=False)
        except Exception as e:
            print(f"    [Kronos] ⚠️  {code} CSV缓存保存失败: {e}")
        return df

    # 3. 同花顺HTTP API（备用，静默失败）
    try:
        df = fetch_kline_from_10jqka(code)
    except Exception:
        df = None
    if df is not None and len(df) >= 30:
        df = df.sort_values('date').tail(lookback)
        try:
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            df.to_csv(csv_path, index=False)
        except Exception:
            pass
        print(f"    [Kronos] 🌐 {code} 同花顺: {len(df)}天")
        return df

    return None


# ============================================================================
# Kronos 预测
# ============================================================================

def load_kronos_model():
    """
    加载Kronos模型

    返回：predictor 对象，加载失败则抛出异常
    """
    from model import Kronos, KronosTokenizer, KronosPredictor

    print(f"  [Kronos] 加载 Kronos-base 模型...")
    print(f"    Tokenizer: {TOKENIZER_PATH}")
    print(f"    Model:     {MODEL_PATH}")

    if not os.path.exists(TOKENIZER_PATH):
        raise FileNotFoundError(
            f"Kronos Tokenizer 不存在: {TOKENIZER_PATH}\n"
            f"请先运行: huggingface-cli download NeoQuasar/Kronos-Tokenizer-base --local-dir {TOKENIZER_PATH}"
        )

    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(
            f"Kronos Model 不存在: {MODEL_PATH}\n"
            f"请先运行: huggingface-cli download NeoQuasar/Kronos-base --local-dir {MODEL_PATH}"
        )

    tokenizer = KronosTokenizer.from_pretrained(TOKENIZER_PATH)
    model = Kronos.from_pretrained(MODEL_PATH)
    predictor = KronosPredictor(model, tokenizer, max_context=512)
    print(f"  [Kronos] ✅ 模型加载完成 (102.3M 参数)")
    return predictor


def predict_single_stock(code: str, predictor, pred_days: int = 5) -> Optional[float]:
    """
    预测单只股票未来5日平均涨跌幅

    返回：预测5日平均涨跌幅(%)，失败返回 None
    """
    df = fetch_kline_data(code, lookback=400)
    if df is None:
        return None

    current_price = df['close'].iloc[-1]
    x_timestamp = pd.to_datetime(df['date'])

    # 生成未来交易日时间戳
    last_date = x_timestamp.iloc[-1]
    future_dates = []
    current = last_date + timedelta(days=1)
    while len(future_dates) < pred_days:
        if current.weekday() < 5:
            future_dates.append(current)
        current += timedelta(days=1)
    y_timestamp = pd.Series(future_dates)

    x_df = df[['open', 'high', 'low', 'close', 'volume', 'amount']].copy()

    try:
        pred_df = predictor.predict(
            df=x_df,
            x_timestamp=x_timestamp,
            y_timestamp=y_timestamp,
            pred_len=pred_days,
            T=0.5,
            top_p=0.9,
            sample_count=5
        )

        if pred_df is None or pred_df.empty:
            print(f"    [Kronos] ❌ {code} 预测结果为空")
            return None

        pred_avg_close = pred_df['close'].mean()
        change_pct = (pred_avg_close - current_price) / current_price * 100
        return round(float(change_pct), 2)

    except Exception as e:
        print(f"    [Kronos] ❌ {code} 预测异常: {e}")
        return None


def fetch_kronos_predictions(codes: list[str], pred_days: int = 5) -> dict[str, float]:
    """
    批量获取Kronos预测

    参数：
    - codes: 股票代码列表
    - pred_days: 预测天数（默认5日）

    返回：{股票代码: 预测5日平均涨跌幅(%)}
    注意：预测失败的股票不会包含在结果中。
    """
    if not codes:
        return {}

    print(f"\n  [Kronos] 开始预测 {len(codes)} 只股票（{pred_days}日）...")

    # 加载模型（失败则直接抛出异常，不回退）
    try:
        predictor = load_kronos_model()
    except Exception as e:
        print(f"  [Kronos] ❌ Kronos模型加载失败，无法继续预测")
        print(f"  [Kronos]    错误详情: {e}")
        return {}

    # 批量预测（静默失败，只报告统计）
    results = {}
    failed = []
    for i, code in enumerate(codes):
        if i % 50 == 0:
            print(f"    [Kronos] 进度: {i}/{len(codes)}")

        prediction = predict_single_stock(code, predictor, pred_days)
        if prediction is not None:
            results[code] = prediction
            print(f"    [Kronos] ✅ {code} 预测: {prediction:+.2f}%")
        else:
            failed.append(code)
