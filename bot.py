# Multi-Strategy Portfolio Manager - Available Strategies
# NOTE: Currently focused on Flow strategy exclusively
# from thunder import thunder  # Strategy 1: Liquidity & Inducement (commented out)
# from ringer import ringer  # Strategy 2: Fibonacci Reversal (commented out)
from flow import flow          # Strategy 3: Trend Continuation (ACTIVE)

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
log_filename = f'logs/bot_log_{datetime.now().strftime("%Y%m%d")}.txt'
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
        self.check_interval = 60  # Main loop interval
        self.market_status_cache = {}  # Cache market status
        self.last_market_check = None  # Track last market status check
        self.strategies = []  # NEW: List of strategy instances
        
        logging.info("="*60)
        logging.info("INITIALIZING MT5 TRADING BOT - MULTI-STRATEGY PORTFOLIO MANAGER")
        logging.info("="*60)
        
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
            self.alerts_ref = db.reference(f'users/{USER_ID}/alerts')  # New: for Thunder alerts
            self.market_status_ref = db.reference(f'users/{USER_ID}/market_status')  # NEW: Market status tracking
            
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
                'bot_version': 'Multi-Strategy Portfolio Manager - Thunder v5.0 + Ringer + Flow'
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
                    logging.info("▶  Bot STARTED via mobile command")
                    
                elif action == 'stop':
                    self.is_running = False
                    self.update_status({
                        'is_running': False,
                        'last_stopped': datetime.now().isoformat()
                    })
                    logging.info("⏸  Bot STOPPED via mobile command")
                    
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
    
    def check_market_status(self):
        """Check if market is open for trading"""
        market_status = {}
        
        for symbol in SYMBOLS:
            symbol_info = mt5.symbol_info(symbol)
            
            if symbol_info is None:
                market_status[symbol] = "UNAVAILABLE"
                continue
            
            # Check if trading is allowed
            if symbol_info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
                market_status[symbol] = "CLOSED"
            elif symbol_info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL:
                # Check session (is market actually open now?)
                tick = mt5.symbol_info_tick(symbol)
                if tick and tick.time > 0:
                    # Get current time vs last tick time
                    current_time = datetime.now().timestamp()
                    time_diff = current_time - tick.time
                    
                    # If last tick was within 5 minutes, market is likely open
                    if time_diff < 300:  # 5 minutes
                        market_status[symbol] = "OPEN"
                    else:
                        market_status[symbol] = "CLOSED"
                else:
                    market_status[symbol] = "CLOSED"
            else:
                market_status[symbol] = "RESTRICTED"
        
        return market_status
    
    # NEW: Enhanced market status check with caching and Firebase updates
    def get_current_market_status(self, force_refresh=False):
        """
        Get current market status with intelligent caching
        Only refreshes every 60 seconds unless force_refresh=True
        Updates Firebase for mobile app visibility
        """
        now = datetime.now()
        
        # Check if we need to refresh (cache expires after 60 seconds)
        if (force_refresh or 
            self.last_market_check is None or 
            (now - self.last_market_check).total_seconds() >= 60):
            
            logging.info(" Refreshing market status...")
            self.market_status_cache = self.check_market_status()
            self.last_market_check = now
            
            # Update Firebase with current market status
            try:
                market_status_data = {
                    'timestamp': now.isoformat(),
                    'status': self.market_status_cache,
                    'overall_open': any(status == "OPEN" for status in self.market_status_cache.values()),
                    'all_closed': all(status == "CLOSED" for status in self.market_status_cache.values())
                }
                self.market_status_ref.set(market_status_data)
                logging.debug(" Market status updated in Firebase")
            except Exception as e:
                logging.error(f" Failed to update market status in Firebase: {e}")
        
        return self.market_status_cache
    
    # NEW: Check if any markets are open for trading
    def is_any_market_open(self):
        """Check if at least one market is open"""
        market_status = self.get_current_market_status()
        return any(status == "OPEN" for status in market_status.values())
    
    # NEW: Get list of currently open markets
    def get_open_markets(self):
        """Return list of symbols that are currently open"""
        market_status = self.get_current_market_status()
        return [symbol for symbol, status in market_status.items() if status == "OPEN"]
    
    def health_check(self):
        """Verify all systems are operational - runs every 10 minutes"""
        logging.info(" Running health check...")
        
        health = {
            'timestamp': datetime.now().isoformat(),
            'mt5_connected': False,
            'firebase_connected': False,
            'symbols_available': {},
            'account_trade_allowed': False,
            'market_status': {},
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
        
        # UPDATED: Use the new market status method with force refresh
        market_status = self.get_current_market_status(force_refresh=True)
        
        # Check symbols availability and market status
        for symbol in SYMBOLS:
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is not None:
                tick = mt5.symbol_info_tick(symbol)
                health['symbols_available'][symbol] = tick is not None
                health['market_status'][symbol] = market_status.get(symbol, "UNKNOWN")
        
        # Log market status with enhanced formatting
        logging.info(" Market Status:")
        open_count = 0
        closed_count = 0
        for symbol, status in market_status.items():
            if status == "OPEN":
                emoji = "🟢"
                open_count += 1
            elif status == "CLOSED":
                emoji = "🔴"
                closed_count += 1
            else:
                emoji = "⚠️"
            logging.info(f"   {emoji} {symbol}: {status}")
        
        # NEW: Summary line
        logging.info(f" Summary: {open_count} Open | {closed_count} Closed | {len(SYMBOLS)} Total")
        
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
    
    def check_signals(self, config):
        """Check for trading signals using ALL strategies (Multi-Strategy Portfolio Manager)"""
        logging.info(f" Checking signals... ({datetime.now().strftime('%H:%M:%S')})")
        
        # Check market status before analysis
        market_status = self.get_current_market_status()
        open_markets = self.get_open_markets()
        
        if not open_markets:
            logging.info(" All markets are CLOSED - Skipping signal analysis")
            logging.info(f"   Next check in {self.check_interval} seconds")
            self.update_status({
                'is_running': True,
                'trading_active': False,
                'market_status': 'ALL_CLOSED',
                'reason': 'Waiting for markets to open'
            })
            return
        
        logging.info(f" {len(open_markets)} market(s) OPEN: {', '.join(open_markets)}")
        
        # Initialize strategies if not already done
        if not self.strategies:
            logging.info(" Initializing Multi-Strategy Portfolio...")
            self.strategies = [
                # thunder(config),  # Commented out - focusing on Flow only
                # ringer(config),   # Commented out - focusing on Flow only
                flow(config),       # ACTIVE: fxalexg's H4/H1 Trend Alignment
            ]
            logging.info(f" Initialized {len(self.strategies)} strateg{'y' if len(self.strategies) == 1 else 'ies'}")
            for strategy in self.strategies:
                logging.info(f"   - {strategy.__class__.__name__}")
        
        # Only analyze symbols that are currently open
        symbols_to_analyze = [s for s in config['symbols'] if s in open_markets]
        
        if not symbols_to_analyze:
            logging.info("  No configured symbols are open right now")
            return
        
        # ═══════════════════════════════════════════════════════════════
        # MULTI-STRATEGY SIGNAL COLLECTION & CONFLICT RESOLUTION
        # ═══════════════════════════════════════════════════════════════
        
        for symbol in symbols_to_analyze:
            try:
                # Check individual symbol status
                symbol_status = market_status.get(symbol, "UNKNOWN")
                if symbol_status != "OPEN":
                    logging.info(f"   [{symbol}] Market is {symbol_status} - Skipping")
                    continue
                
                # Check if we already have a position
                positions_count = self.count_open_positions(symbol)
                if positions_count > 0:
                    logging.info(f"   [{symbol}] Already have {positions_count} position(s) - skipping")
                    continue
                
                # ─── COLLECT SIGNALS FROM ALL STRATEGIES ───
                signals = []
                logging.info(f"   [{symbol}] Analyzing with {len(self.strategies)} strateg{'y' if len(self.strategies) == 1 else 'ies'}...")
                
                for strategy in self.strategies:
                    strategy_name = strategy.__class__.__name__
                    try:
                        signal = strategy.analyze(symbol)
                        
                        if signal:
                            # Inject strategy name into signal
                            signal['strategy_name'] = strategy_name
                            signal['symbol'] = symbol
                            signals.append(signal)
                            
                            logging.info(f"       {strategy_name}: {signal['direction']} | P: {signal.get('probability', 'N/A')}")
                        else:
                            logging.debug(f"       {strategy_name}: No setup")
                            
                    except Exception as e:
                        logging.error(f"       {strategy_name} error: {e}")
                        traceback.print_exc()
                
                # ─── CONFLICT RESOLUTION ───
                if len(signals) == 0:
                    logging.info(f"   [{symbol}] No signals from any strategy")
                    continue
                
                elif len(signals) == 1:
                    # Single signal - no conflict
                    chosen_signal = signals[0]
                    logging.info(f"   [{symbol}]  Single signal: {chosen_signal['strategy_name']} → {chosen_signal['direction']}")
                    
                else:
                    # Multiple signals - check for conflicts
                    directions = set(s['direction'] for s in signals)
                    
                    if len(directions) > 1:
                        # CONFLICT DETECTED: BUY vs SELL
                        logging.warning(f"   [{symbol}]   CONFLICT DETECTED - SKIPPING ALL SIGNALS:")
                        for s in signals:
                            logging.warning(f"      - {s['strategy_name']}: {s['direction']}")
                        logging.warning(f"   [{symbol}] Risk management: Skipping both to avoid whipsaw")
                        continue  # Skip this symbol entirely
                    
                    else:
                        # All signals agree on direction - take the first/highest confidence
                        chosen_signal = signals[0]
                        logging.info(f"   [{symbol}]  {len(signals)} strategies AGREE on {chosen_signal['direction']}:")
                        for s in signals:
                            logging.info(f"      - {s['strategy_name']} | P: {s.get('probability', 'N/A')}")
                        logging.info(f"   [{symbol}] Selecting: {chosen_signal['strategy_name']}")
                
                # ─── SEND ALERT TO FIREBASE ───
                if chosen_signal:
                    logging.info(f" [{symbol}] Sending {chosen_signal['strategy_name']} alert to Firebase...")
                    logging.info(f"   Direction: {chosen_signal['direction']}")
                    logging.info(f"   Probability: {chosen_signal.get('probability', 'N/A')}")
                    logging.info(f"   Entry: {chosen_signal.get('entry_price', 'N/A'):.5f}")
                    logging.info(f"   SL: {chosen_signal.get('suggested_sl', 'N/A'):.5f}")
                    logging.info(f"   TP: {chosen_signal.get('suggested_tp', 'N/A'):.5f}")
                    
                    try:
                        self.alerts_ref.push({
                            **chosen_signal,
                            'alert_type': 'MULTI_STRATEGY_SETUP',
                            'action_required': 'REVIEW_AND_EXECUTE',
                            'sent_at': datetime.now().isoformat(),
                            'market_status': 'OPEN',
                            'num_strategies_agree': len([s for s in signals if s['direction'] == chosen_signal['direction']])
                        })
                        logging.info(f"    Alert sent to Firebase /alerts")
                    except Exception as e:
                        logging.error(f"    Failed to send alert to Firebase: {e}")
                
            except Exception as e:
                logging.error(f" Error analyzing {symbol}: {e}")
                traceback.print_exc()
        
        # Update Firebase status
        self.update_status({
            'is_running': True,
            'trading_active': True,
            'open_markets': open_markets,
            'markets_analyzed': symbols_to_analyze,
            'active_strategies': [s.__class__.__name__ for s in self.strategies]
        })
    
    def execute_thunder_signal(self, symbol, signal, config):
        """Execute a signal from any strategy (for auto-trading if enabled)"""
        try:
            strategy_name = signal.get('strategy_name', 'UNKNOWN')
            
            # Final market status verification before execution
            market_status = self.get_current_market_status(force_refresh=True)
            if market_status.get(symbol) != "OPEN":
                logging.warning(f"  Market for {symbol} is {market_status.get(symbol)} - Cannot execute trade")
                return None
            
            logging.info(f" Executing {strategy_name} signal: {signal['direction']} on {symbol}")
            
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
            
            # Use strategy's suggested entry, SL, TP
            entry_price = signal['entry_price']
            sl = signal['suggested_sl']
            tp = signal['suggested_tp']
            lot = config['lot_size']
            
            if signal['direction'] == "BUY":
                order_type = mt5.ORDER_TYPE_BUY
                price = tick.ask
            else:  # SELL
                order_type = mt5.ORDER_TYPE_SELL
                price = tick.bid
            
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
                "magic": 234001,  # Multi-strategy magic number
                "comment": f"{strategy_name}_bot",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            logging.info(f"   Price: {price}")
            logging.info(f"   SL: {sl}")
            logging.info(f"   TP: {tp}")
            logging.info(f"   Volume: {lot}")
            
            # Send order
            result = mt5.order_send(request)
            
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logging.info(f" {strategy_name} order executed successfully")
                logging.info(f"   Ticket: {result.order}")
                
                # Verify trade
                trade_verified = self.verify_trade(result.order, symbol)
                
                if trade_verified:
                    # Log to Firebase trades
                    trade_data = {
                        'timestamp': datetime.now().isoformat(),
                        'symbol': symbol,
                        'type': signal['direction'],
                        'entry_price': price,
                        'lot_size': lot,
                        'sl': sl,
                        'tp': tp,
                        'ticket': result.order,
                        'verified': True,
                        'strategy': strategy_name,  # NEW: Track which strategy generated this trade
                        'probability': signal.get('probability', 'N/A'),
                        'zone_type': signal.get('zone_type', 'N/A'),
                        'market_status_at_entry': 'OPEN'
                    }
                    self.trades_ref.push(trade_data)
                
                return result
            else:
                logging.error(f" {strategy_name} order failed: Code {result.retcode}")
                logging.error(f"   Comment: {result.comment}")
                return None
                
        except Exception as e:
            logging.error(f" Error executing signal: {e}")
            traceback.print_exc()
            return None
    
    def run(self):
        """Main bot loop"""
        logging.info("="*60)
        logging.info(" MULTI-STRATEGY PORTFOLIO MANAGER STARTED")
        logging.info("="*60)
        logging.info(f"Check interval: {self.check_interval} seconds")
        logging.info(f"Health check interval: {self.health_check_interval} seconds")
        logging.info(f"Symbols: {SYMBOLS}")
        logging.info(f"Timeframe: {TIMEFRAME}")
        logging.info(f"Strategies: Thunder v5.0 (JeaFx) + Ringer + Flow")
        logging.info("="*60)
        
        self.is_running = True
        self.update_status({'is_running': True})
        
        # Initial health check
        self.health_check()
        
        # NEW: Initial market status check
        initial_market_status = self.get_current_market_status(force_refresh=True)
        logging.info(f" Initial Market Status: {initial_market_status}")
        
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
                
                if config is None:
                    logging.warning("  Failed to get config from Firebase - using defaults")
                    config = {
                        'symbols': SYMBOLS,
                        'timeframe': TIMEFRAME,
                        'lot_size': LOT_SIZE,
                        'max_trades': MAX_TRADES,
                        'stop_loss_pips': STOP_LOSS_PIPS,
                        'take_profit_pips': TAKE_PROFIT_PIPS
                    }
                
                # Update account status for mobile
                account_info = self.get_account_info()
                if account_info:
                    # NEW: Include market status in status updates
                    is_market_open = self.is_any_market_open()
                    self.update_status({
                        'is_running': True,
                        'balance': account_info['balance'],
                        'equity': account_info['equity'],
                        'profit': account_info['profit'],
                        'open_positions': self.count_open_positions(),
                        'market_open': is_market_open,  # NEW
                        'open_markets': self.get_open_markets()  # NEW
                    })
                
                # Check for trading signals using Thunder
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