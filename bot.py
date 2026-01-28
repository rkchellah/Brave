import MetaTrader5 as mt5
import firebase_admin
from firebase_admin import credentials, db
import numpy as np
import time
import logging
from datetime import datetime, timedelta
import traceback

# Import your configuration
from config import (
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER,
    FIREBASE_DATABASE_URL, USER_ID,
    SYMBOLS, TIMEFRAME, LOT_SIZE, MAX_TRADES,
    STOP_LOSS_PIPS, TAKE_PROFIT_PIPS
)

# Setup logging
log_filename = f'bot_log_{datetime.now().strftime("%Y%m%d")}.txt'
logging.basicConfig(
    filename=log_filename,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Also log to console
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger('').addHandler(console)

class MT5TradingBot:
    def __init__(self):
        self.is_running = False
        self.last_health_check = datetime.now()
        self.health_check_interval = 600  # 10 minutes in seconds
        self.check_interval = 20  # Main loop interval
        
        logging.info("="*20)
        logging.info("INITIALIZING MT5 TRADING BOT - PHASE 1")
        logging.info("="*20)
        
        # Initialize connections
        if not self.initialize_mt5():
            raise Exception("MT5 initialization failed")
        
        if not self.initialize_firebase():
            raise Exception("Firebase initialization failed")
        
        logging.info(" Bot initialization complete")
    
    def initialize_mt5(self):
        """Initialize MT5 connection and login"""
        logging.info("Initializing MT5...")
        
        if not mt5.initialize():
            logging.error(" MT5 initialization failed")
            return False
        
        logging.info(" MT5 initialized")
        
        # Login to account
        authorized = mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
        
        if not authorized:
            error = mt5.last_error()
            logging.error(f" MT5 login failed: {error}")
            return False
        
        account_info = mt5.account_info()
        logging.info(f" Logged in to MT5")
        logging.info(f"   Account: {account_info.login}")
        logging.info(f"   Server: {account_info.server}")
        logging.info(f"   Balance: ${account_info.balance}")
        logging.info(f"   Leverage: 1:{account_info.leverage}")
        
        return True
    
    def initialize_firebase(self):
        """Initialize Firebase and set up real-time listeners"""
        logging.info("Initializing Firebase...")
        
        try:
            cred = credentials.Certificate("serviceAccountKey.json")
            firebase_admin.initialize_app(cred, {
                'databaseURL': FIREBASE_DATABASE_URL
            })
            
            # Set up database references
            self.config_ref = db.reference(f'users/{USER_ID}/bot_config')
            self.status_ref = db.reference(f'users/{USER_ID}/bot_status')
            self.command_ref = db.reference(f'users/{USER_ID}/commands')
            self.trades_ref = db.reference(f'users/{USER_ID}/trades')
            self.health_ref = db.reference(f'users/{USER_ID}/health')
            
            # Initialize config if not exists
            if self.config_ref.get() is None:
                self.config_ref.set({
                    'symbols': SYMBOLS,
                    'timeframe': TIMEFRAME,
                    'lot_size': LOT_SIZE,
                    'max_trades': MAX_TRADES,
                    'stop_loss_pips': STOP_LOSS_PIPS,
                    'take_profit_pips': TAKE_PROFIT_PIPS
                })
                logging.info("   Initialized default config in Firebase")
            
            # Set up command listener for mobile control
            self.command_ref.listen(self.handle_command)
            logging.info(" Firebase connected - Command listener active")
            
            # Initial status update
            self.update_status({
                'is_running': False,
                'last_started': None,
                'bot_version': 'Phase 1 - v1.0'
            })
            
            return True
            
        except Exception as e:
            logging.error(f" Firebase initialization failed: {e}")
            traceback.print_exc()
            return False
    
    def handle_command(self, event):
        """Handle commands from Firebase (mobile app)"""
        try:
            command = event.data
            if command:
                action = command.get('action')
                timestamp = command.get('timestamp', 'unknown')
                
                logging.info(f" Command received: {action} (timestamp: {timestamp})")
                
                if action == 'start':
                    self.is_running = True
                    self.update_status({
                        'is_running': True,
                        'last_started': datetime.now().isoformat()
                    })
                    logging.info("  Bot STARTED via mobile command")
                    
                elif action == 'stop':
                    self.is_running = False
                    self.update_status({
                        'is_running': False,
                        'last_stopped': datetime.now().isoformat()
                    })
                    logging.info("  Bot STOPPED via mobile command")
                    
        except Exception as e:
            logging.error(f"Error handling command: {e}")
    
    def validate_price_data(self, rates, symbol, expected_count):
        """Validate that price data is complete and has no gaps"""
        
        if rates is None:
            logging.error(f" [{symbol}] No data received")
            return False
        
        if len(rates) < expected_count:
            logging.error(f" [{symbol}] Insufficient data: {len(rates)}/{expected_count} candles")
            return False
        
        # Check for time gaps (for M15, gap should be ~900 seconds)
        timeframe_map = {
            'M1': 60, 'M5': 300, 'M15': 900, 'M30': 1800,
            'H1': 3600, 'H4': 14400, 'D1': 86400
        }
        expected_gap = timeframe_map.get(TIMEFRAME, 900)
        
        time_diffs = [rates[i+1]['time'] - rates[i]['time'] for i in range(len(rates)-1)]
        
        for i, diff in enumerate(time_diffs):
            if diff > expected_gap * 2:
                logging.warning(f"  [{symbol}] Time gap detected: {diff}s at index {i}")
        
        # Check for invalid prices (zero or negative)
        if any(rate['close'] <= 0 for rate in rates):
            logging.error(f" [{symbol}] Invalid price data detected (zero/negative)")
            return False
        
        logging.debug(f" [{symbol}] Data validated: {len(rates)} candles, no gaps")
        return True
    
    def health_check(self):
        """Verify all systems are operational - runs every 10 minutes"""
        logging.info(" Running health check...")
        
        health = {
            'timestamp': datetime.now().isoformat(),
            'mt5_connected': False,
            'firebase_connected': False,
            'symbols_available': {},
            'account_trade_allowed': False,
            'status': 'UNKNOWN'
        }
        
        # Check MT5 connection
        terminal_info = mt5.terminal_info()
        if terminal_info is not None:
            health['mt5_connected'] = True
            health['mt5_connected_to_broker'] = terminal_info.connected
        
        # Check Firebase connection
        try:
            self.status_ref.get()
            health['firebase_connected'] = True
        except:
            health['firebase_connected'] = False
        
        # Check symbols availability
        for symbol in SYMBOLS:
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is not None:
                tick = mt5.symbol_info_tick(symbol)
                health['symbols_available'][symbol] = tick is not None
        
        # Check account trading permission
        account_info = mt5.account_info()
        if account_info:
            health['account_trade_allowed'] = account_info.trade_allowed
            health['balance'] = account_info.balance
            health['equity'] = account_info.equity
        
        # Overall status
        all_checks = [
            health['mt5_connected'],
            health['firebase_connected'],
            all(health['symbols_available'].values()),
            health['account_trade_allowed']
        ]
        
        if all(all_checks):
            health['status'] = 'HEALTHY'
            logging.info(" Health check PASSED - All systems operational")
        else:
            health['status'] = 'DEGRADED'
            logging.warning("  Health check FAILED - Some systems down")
        
        # Log to Firebase for mobile visibility
        self.health_ref.set(health)
        
        self.last_health_check = datetime.now()
        return health['status'] == 'HEALTHY'
    
    def verify_trade(self, ticket_number, symbol):
        """Verify that a trade was actually opened"""
        logging.info(f" Verifying trade #{ticket_number}...")
        
        # Wait for broker to process
        time.sleep(2)
        
        try:
            positions = mt5.positions_get(ticket=ticket_number)
            
            if positions and len(positions) > 0:
                position = positions[0]
                logging.info(f" Trade verified:")
                logging.info(f"   Ticket: {position.ticket}")
                logging.info(f"   Symbol: {position.symbol}")
                logging.info(f"   Type: {'BUY' if position.type == 0 else 'SELL'}")
                logging.info(f"   Volume: {position.volume}")
                logging.info(f"   Entry: {position.price_open}")
                logging.info(f"   SL: {position.sl}")
                logging.info(f"   TP: {position.tp}")
                return True
            else:
                logging.error(f" Trade verification FAILED: Ticket #{ticket_number} not found")
                return False
                
        except Exception as e:
            logging.error(f" Error verifying trade: {e}")
            return False
    
    def get_config(self):
        """Get current configuration from Firebase"""
        return self.config_ref.get()
    
    def update_status(self, status_data):
        """Update bot status in Firebase for mobile app"""
        try:
            status_data['last_updated'] = datetime.now().isoformat()
            self.status_ref.update(status_data)
        except Exception as e:
            logging.error(f"Error updating status: {e}")
    
    def get_account_info(self):
        """Get MT5 account information"""
        account_info = mt5.account_info()
        if account_info:
            return {
                'balance': account_info.balance,
                'equity': account_info.equity,
                'margin': account_info.margin,
                'free_margin': account_info.margin_free,
                'profit': account_info.profit
            }
        return None
    
    def count_open_positions(self, symbol=None):
        """Count open positions"""
        if symbol:
            positions = mt5.positions_get(symbol=symbol)
        else:
            positions = mt5.positions_get()
        
        return len(positions) if positions else 0
    
    def get_trading_signal(self, symbol, config):
        """
        Calculate trading signal using Moving Average crossover
        This will be replaced with Supply & Demand logic in Phase 2
        """
        try:
            # Map timeframe string to MT5 constant
            timeframe_map = {
                'M1': mt5.TIMEFRAME_M1, 'M5': mt5.TIMEFRAME_M5,
                'M15': mt5.TIMEFRAME_M15, 'M30': mt5.TIMEFRAME_M30,
                'H1': mt5.TIMEFRAME_H1, 'H4': mt5.TIMEFRAME_H4,
                'D1': mt5.TIMEFRAME_D1
            }
            timeframe = timeframe_map.get(config.get('timeframe', 'M15'), mt5.TIMEFRAME_M15)
            
            # Parameters for MA crossover
            fast_period = 20
            slow_period = 50
            candles_needed = slow_period + 5
            
            # Get historical data
            rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, candles_needed)
            
            # Validate data
            if not self.validate_price_data(rates, symbol, candles_needed):
                return None
            
            # Extract close prices
            closes = [rate['close'] for rate in rates]
            
            # Calculate current MAs
            fast_ma_current = np.mean(closes[-fast_period:])
            slow_ma_current = np.mean(closes[-slow_period:])
            
            # Calculate previous MAs (for crossover detection)
            fast_ma_previous = np.mean(closes[-fast_period-1:-1])
            slow_ma_previous = np.mean(closes[-slow_period-1:-1])
            
            # Detect crossover
            if fast_ma_previous <= slow_ma_previous and fast_ma_current > slow_ma_current:
                logging.info(f" [{symbol}] BUY signal: Fast MA crossed above Slow MA")
                return "BUY"
            
            if fast_ma_previous >= slow_ma_previous and fast_ma_current < slow_ma_current:
                logging.info(f" [{symbol}] SELL signal: Fast MA crossed below Slow MA")
                return "SELL"
            
            return None
            
        except Exception as e:
            logging.error(f"Error calculating signal for {symbol}: {e}")
            return None
    
    def open_trade(self, symbol, signal, config):
        """Execute a trade"""
        try:
            logging.info(f" Attempting to open {signal} position on {symbol}")
            
            # Check max trades limit
            open_positions = self.count_open_positions()
            if open_positions >= config['max_trades']:
                logging.warning(f"  Max trades limit reached ({config['max_trades']})")
                return None
            
            # Get current price
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                logging.error(f" Failed to get tick for {symbol}")
                return None
            
            # Get symbol info
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                logging.error(f" Symbol {symbol} not found")
                return None
            
            # Calculate lot size and SL/TP
            lot = config['lot_size']
            point = symbol_info.point
            sl_points = config['stop_loss_pips']
            tp_points = config['take_profit_pips']
            
            if signal == "BUY":
                price = tick.ask
                sl = price - sl_points * point * 10
                tp = price + tp_points * point * 10
                order_type = mt5.ORDER_TYPE_BUY
            else:  # SELL
                price = tick.bid
                sl = price + sl_points * point * 10
                tp = price - tp_points * point * 10
                order_type = mt5.ORDER_TYPE_SELL
            
            # Prepare order request
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot,
                "type": order_type,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": 20,
                "magic": 234000,
                "comment": "bot_phase1",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            logging.info(f"   Price: {price}")
            logging.info(f"   SL: {sl} ({sl_points} pips)")
            logging.info(f"   TP: {tp} ({tp_points} pips)")
            logging.info(f"   Volume: {lot}")
            
            # Send order
            result = mt5.order_send(request)
            
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logging.info(f" Order executed successfully")
                logging.info(f"   Ticket: {result.order}")
                
                # VERIFY TRADE - Do not assume success
                trade_verified = self.verify_trade(result.order, symbol)
                
                if trade_verified:
                    # Log to Firebase
                    trade_data = {
                        'timestamp': datetime.now().isoformat(),
                        'symbol': symbol,
                        'type': signal,
                        'entry_price': price,
                        'lot_size': lot,
                        'sl': sl,
                        'tp': tp,
                        'ticket': result.order,
                        'verified': True
                    }
                    self.trades_ref.push(trade_data)
                else:
                    logging.error(" Trade execution reported success but verification failed!")
                
                return result
            else:
                logging.error(f" Order failed: Code {result.retcode}")
                logging.error(f"   Comment: {result.comment}")
                return None
                
        except Exception as e:
            logging.error(f" Error opening trade: {e}")
            traceback.print_exc()
            return None
    
    def check_signals(self, config):
        """Check for trading signals on all symbols"""
        logging.info(f" Checking signals... ({datetime.now().strftime('%H:%M:%S')})")
        
        for symbol in config['symbols']:
            try:
                # Check if we already have a position
                positions_count = self.count_open_positions(symbol)
                
                if positions_count > 0:
                    logging.info(f"   [{symbol}] Already have {positions_count} position(s) - skipping")
                    continue
                
                # Get signal
                signal = self.get_trading_signal(symbol, config)
                
                if signal in ['BUY', 'SELL']:
                    logging.info(f" [{symbol}] Signal detected: {signal}")
                    self.open_trade(symbol, signal, config)
                else:
                    logging.info(f"   [{symbol}] No signal - waiting for crossover")
                    
            except Exception as e:
                logging.error(f" Error checking {symbol}: {e}")
    
    def run(self):
        """Main bot loop - runs every 60 seconds"""
        logging.info("="*0)
        logging.info(" TRADING BOT STARTED - PHASE 1")
        logging.info("="*20)
        logging.info(f"Check interval: {self.check_interval} seconds")
        logging.info(f"Health check interval: {self.health_check_interval} seconds")
        logging.info(f"Symbols: {SYMBOLS}")
        logging.info(f"Timeframe: {TIMEFRAME}")
        logging.info("="*20)
        
        self.is_running = True
        self.update_status({'is_running': True})
        
        # Initial health check
        self.health_check()
        
        try:
            while True:
                # Check if bot should be running (mobile control)
                if not self.is_running:
                    logging.info("⏸  Bot is paused - waiting for start command...")
                    time.sleep(self.check_interval)
                    continue
                
                # Periodic health check (every 10 minutes)
                if (datetime.now() - self.last_health_check).total_seconds() >= self.health_check_interval:
                    healthy = self.health_check()
                    if not healthy:
                        logging.warning("  Health check failed - continuing but review logs")
                
                # Get current configuration
                config = self.get_config()
                
                # Update account status for mobile
                account_info = self.get_account_info()
                if account_info:
                    self.update_status({
                        'is_running': True,
                        'balance': account_info['balance'],
                        'equity': account_info['equity'],
                        'profit': account_info['profit'],
                        'open_positions': self.count_open_positions()
                    })
                
                # Check for trading signals
                self.check_signals(config)
                
                # Sleep until next check
                logging.info(f" Sleeping for {self.check_interval} seconds...\n")
                time.sleep(self.check_interval)
                
        except KeyboardInterrupt:
            logging.info("\n  Bot stopped by user (Ctrl+C)")
        except Exception as e:
            logging.error(f" Critical error in main loop: {e}")
            traceback.print_exc()
        finally:
            self.is_running = False
            self.update_status({'is_running': False})
            mt5.shutdown()
            logging.info(" Bot shut down - MT5 connection closed")

if __name__ == "__main__":
    try:
        bot = MT5TradingBot()
        bot.run()
    except Exception as e:
        logging.error(f"Failed to start bot: {e}")
        traceback.print_exc()