import MetaTrader5 as mt5
import numpy as np

def get_trading_signal(symbol, config):
    try:
        timeframe = mt5.TIMEFRAME_M15
        fast_period = 20
        slow_period = 50
        
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, slow_period + 5)
        
        if rates is None or len(rates) < slow_period + 2:
            return None
        
        closes = [rate['close'] for rate in rates]
        
        fast_ma_current = np.mean(closes[-fast_period:])
        slow_ma_current = np.mean(closes[-slow_period:])
        fast_ma_previous = np.mean(closes[-fast_period-1:-1])
        slow_ma_previous = np.mean(closes[-slow_period-1:-1])
        
        if fast_ma_previous <= slow_ma_previous and fast_ma_current > slow_ma_current:
            return "BUY"
        
        if fast_ma_previous >= slow_ma_previous and fast_ma_current < slow_ma_current:
            return "SELL"
        
        return None
        
    except Exception as e:
        print(f"Error: {e}")
        return None

def should_close_trade(position, config):
    return False