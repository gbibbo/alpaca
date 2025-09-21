#!/usr/bin/env python3
"""
Sistema Híbrido de Trading: Backtrader + Alpaca
VERSION: 3.0 - Sin dependencias problemáticas
"""

import os
import warnings
warnings.filterwarnings('ignore')

import pandas as pd
import numpy as np
import backtrader as bt
import yfinance as yf
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Alpaca API (solo para live/paper)
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

load_dotenv()

MODE = os.environ.get("MODE", "backtest")  # "backtest" | "paper" | "live"
SYMBOL = os.environ.get("SYMBOL", "AAPL")
RISK_PCT = float(os.environ.get("RISK_PCT", "0.20"))

print("=== SISTEMA HÍBRIDO TRADING ===")
print(f"MODE: {MODE} | SYMBOL: {SYMBOL}")
print("=" * 40)

class SmartStrategy(bt.Strategy):
    """
    Estrategia inteligente basada en indicadores técnicos
    (Similar al agente que ya funciona)
    """
    
    params = dict(
        risk_pct=RISK_PCT,
        sma_short=20,
        sma_long=50,
        rsi_period=14,
        rsi_low=30,
        rsi_high=70
    )
    
    def log(self, txt, dt=None):
        """Logging con timestamp"""
        dt = dt or self.datas[0].datetime.date(0)
        print(f'{dt.isoformat()} {txt}')
    
    def __init__(self):
        # Referencias a datos
        self.dataclose = self.datas[0].close
        self.datahigh = self.datas[0].high
        self.datalow = self.datas[0].low
        
        # IMPORTANTE: Inicializar orden pendiente
        self.order = None
        
        # Indicadores técnicos
        self.sma_short = bt.indicators.SimpleMovingAverage(
            self.datas[0], period=self.params.sma_short)
        self.sma_long = bt.indicators.SimpleMovingAverage(
            self.datas[0], period=self.params.sma_long)
        self.rsi = bt.indicators.RelativeStrengthIndex(
            self.datas[0], period=self.params.rsi_period)
        self.macd = bt.indicators.MACD(self.datas[0])
        
        # Crossover signals
        self.sma_crossover = bt.indicators.CrossOver(self.sma_short, self.sma_long)
        
        # Contadores
        self.order_count = 0
        self.buy_count = 0
        self.sell_count = 0
        
    def notify_order(self, order):
        """Notificación de órdenes"""
        if order.status in [order.Completed]:
            if order.isbuy():
                self.log(f'BUY EXECUTED - Price: {order.executed.price:.2f}, Size: {order.executed.size}')
                self.buy_count += 1
            elif order.issell():
                self.log(f'SELL EXECUTED - Price: {order.executed.price:.2f}, Size: {order.executed.size}')
                self.sell_count += 1
                
            # IMPORTANTE: Resetear orden cuando se complete
            self.order = None
            
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            self.log(f'ORDER FAILED - {order.getstatusname()}')
            # IMPORTANTE: Resetear orden cuando falle
            self.order = None
    
    def notify_trade(self, trade):
        """Notificación de trades cerrados"""
        if not trade.isclosed:
            return
        self.log(f'TRADE PnL: {trade.pnl:.2f}')
    
    def next(self):
        """Lógica principal de la estrategia"""
        
        # Evitar órdenes múltiples
        if self.order:
            return
            
        # Obtener valores actuales
        close = self.dataclose[0]
        rsi = self.rsi[0]
        
        # SEÑALES DE COMPRA
        if not self.position:  # No tenemos posición
            buy_signals = 0
            
            # Señal 1: Tendencia alcista (SMA)
            if self.sma_crossover[0] > 0:  # SMA corta cruza arriba de larga
                buy_signals += 1
                
            # Señal 2: RSI sobrevendido pero recuperándose
            if self.rsi[0] > self.params.rsi_low and self.rsi[-1] <= self.params.rsi_low:
                buy_signals += 1
                
            # Señal 3: MACD alcista
            if self.macd.macd[0] > self.macd.signal[0]:
                buy_signals += 1
            
            # Ejecutar compra si hay suficientes señales
            if buy_signals >= 2:
                cash = self.broker.get_cash()
                size = int((cash * self.params.risk_pct) / close)
                
                if size > 0:
                    self.log(f'BUY SIGNAL - Signals: {buy_signals}/3, RSI: {rsi:.1f}')
                    self.order = self.buy(size=size)
                    self.order_count += 1
        
        # SEÑALES DE VENTA
        else:  # Tenemos posición
            sell_signals = 0
            
            # Señal 1: Tendencia bajista
            if self.sma_crossover[0] < 0:  # SMA corta cruza abajo de larga
                sell_signals += 1
                
            # Señal 2: RSI sobrecomprado
            if rsi > self.params.rsi_high:
                sell_signals += 1
                
            # Señal 3: MACD bajista
            if self.macd.macd[0] < self.macd.signal[0]:
                sell_signals += 1
            
            # Ejecutar venta si hay señales
            if sell_signals >= 2:
                self.log(f'SELL SIGNAL - Signals: {sell_signals}/3, RSI: {rsi:.1f}')
                self.order = self.sell(size=self.position.size)
                self.order_count += 1
    
    def stop(self):
        """Resumen final"""
        portfolio_value = self.broker.getvalue()
        self.log('=== ESTRATEGIA COMPLETADA ===')
        self.log(f'Órdenes totales: {self.order_count}')
        self.log(f'Compras: {self.buy_count}')
        self.log(f'Ventas: {self.sell_count}')
        self.log(f'Valor final: ${portfolio_value:.2f}')


def run_backtest_analysis():
    """Backtest completo con análisis detallado"""
    print("\n🔄 EJECUTANDO BACKTEST CON ANÁLISIS...")
    
    try:
        # Configurar Cerebro
        cerebro = bt.Cerebro()
        
        # Descargar datos históricos
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365)  # 1 año
        
        print(f"Descargando {SYMBOL} desde {start_date.date()}")
        
        df = yf.download(SYMBOL, start=start_date, end=end_date, 
                        interval="1d", auto_adjust=True, progress=False)
        
        if df.empty:
            print(f"❌ No hay datos para {SYMBOL}")
            return False
        
        # CORREGIR PROBLEMA DE COLUMNAS MULTIINDEX
        if isinstance(df.columns, pd.MultiIndex):
            # Aplanar MultiIndex tomando solo el primer nivel
            df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
        
        # Asegurar nombres estándar de columnas
        df.columns = [str(col).strip() for col in df.columns]
        
        print(f"Datos descargados: {len(df)} barras")
        print(f"Columnas: {list(df.columns)}")
        
        # Añadir datos a Cerebro
        data = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data)
        
        # Añadir estrategia
        cerebro.addstrategy(SmartStrategy)
        
        # Configurar broker
        initial_cash = 100000.0
        cerebro.broker.setcash(initial_cash)
        cerebro.broker.setcommission(commission=0.001)  # 0.1%
        
        # Añadir analizadores
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe')
        cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')
        cerebro.addanalyzer(bt.analyzers.SQN, _name='sqn')
        
        print(f"Capital inicial: ${initial_cash:,.2f}")
        print("Ejecutando backtest...")
        
        # Ejecutar
        results = cerebro.run()
        strat = results[0]
        
        # Resultados principales
        final_value = cerebro.broker.getvalue()
        total_return = (final_value - initial_cash) / initial_cash * 100
        
        print(f"\n=== RESULTADOS BACKTEST ===")
        print(f"Capital inicial: ${initial_cash:,.2f}")
        print(f"Capital final: ${final_value:,.2f}")
        print(f"Retorno total: {total_return:.2f}%")
        print(f"Ganancia/Pérdida: ${final_value - initial_cash:,.2f}")
        
        # Análisis avanzado
        try:
            analyzers = strat.analyzers
            
            # Sharpe Ratio
            sharpe = analyzers.sharpe.get_analysis().get('sharperatio', 0)
            print(f"Sharpe Ratio: {sharpe:.3f}")
            
            # Drawdown
            dd = analyzers.drawdown.get_analysis()
            max_dd = dd.get('max', {}).get('drawdown', 0)
            print(f"Max Drawdown: {max_dd:.2f}%")
            
            # Trades
            trades = analyzers.trades.get_analysis()
            total_trades = trades.get('total', {}).get('total', 0)
            won = trades.get('won', {}).get('total', 0)
            lost = trades.get('lost', {}).get('total', 0)
            win_rate = (won / total_trades * 100) if total_trades > 0 else 0
            
            print(f"Total trades: {total_trades}")
            print(f"Ganadores: {won} ({win_rate:.1f}%)")
            print(f"Perdedores: {lost}")
            
            if won > 0:
                avg_win = trades.get('won', {}).get('pnl', {}).get('average', 0)
                print(f"Ganancia promedio: ${avg_win:.2f}")
            
            if lost > 0:
                avg_loss = trades.get('lost', {}).get('pnl', {}).get('average', 0)
                print(f"Pérdida promedio: ${avg_loss:.2f}")
                
            # SQN (System Quality Number)
            sqn = analyzers.sqn.get_analysis().get('sqn', 0)
            print(f"SQN: {sqn:.2f}")
            
        except Exception as e:
            print(f"Error en análisis detallado: {e}")
        
        # Evaluación de la estrategia
        print(f"\n=== EVALUACIÓN ===")
        if total_return > 0:
            print("✅ Estrategia RENTABLE")
        else:
            print("❌ Estrategia NO rentable")
            
        if sharpe > 1:
            print("✅ Sharpe Ratio BUENO (>1)")
        elif sharpe > 0.5:
            print("⚠️ Sharpe Ratio MODERADO")
        else:
            print("❌ Sharpe Ratio BAJO")
            
        return True
        
    except Exception as e:
        print(f"❌ Error en backtest: {e}")
        return False


def run_live_paper_trading():
    """Ejecutar trading en vivo usando tu sistema actual (que funciona)"""
    print("\n📈 EJECUTANDO LIVE/PAPER TRADING...")
    print("(Usando sistema alpaca-py que ya funciona)")
    
    try:
        # Reutilizar tu código que ya funciona
        from finrl_basic_agent import SimpleAlpacaTrader
        
        trader = SimpleAlpacaTrader()
        trader.tickers = [SYMBOL]  # Solo el símbolo configurado
        
        print(f"Ejecutando trading para {SYMBOL}...")
        success = trader.run_trading_pipeline()
        
        if success:
            print("✅ Trading ejecutado exitosamente")
            return True
        else:
            print("❌ Error en trading")
            return False
            
    except ImportError:
        print("❌ No se encontró finrl_basic_agent.py")
        print("Copialo al directorio actual o ejecuta desde el directorio correcto")
        return False
    except Exception as e:
        print(f"❌ Error en live trading: {e}")
        return False


def compare_strategies():
    """Comparar diferentes estrategias en backtest"""
    print("\n🔄 COMPARANDO ESTRATEGIAS...")
    
    strategies = [
        ("Random 50/50", "random"),
        ("Buy & Hold", "buyhold"),
        ("Smart Strategy", "smart")
    ]
    
    results = {}
    
    for name, strategy_type in strategies:
        print(f"\nTesting {name}...")
        
        try:
            cerebro = bt.Cerebro()
            
            # Datos
            end_date = datetime.now()
            start_date = end_date - timedelta(days=365)
            df = yf.download(SYMBOL, start=start_date, end=end_date, 
                           interval="1d", auto_adjust=True, progress=False)
            
            # CORREGIR COLUMNAS MULTIINDEX
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
            df.columns = [str(col).strip() for col in df.columns]
            
            data = bt.feeds.PandasData(dataname=df)
            cerebro.adddata(data)
            
            # Seleccionar estrategia
            if strategy_type == "random":
                class RandomStrategy(bt.Strategy):
                    def next(self):
                        if not self.position and np.random.random() < 0.01:  # 1% prob compra
                            size = int(self.broker.getcash() * 0.2 / self.data.close[0])
                            if size > 0:
                                self.buy(size=size)
                        elif self.position and np.random.random() < 0.01:  # 1% prob venta
                            self.sell(size=self.position.size)
                            
                cerebro.addstrategy(RandomStrategy)
                
            elif strategy_type == "buyhold":
                class BuyHoldStrategy(bt.Strategy):
                    def __init__(self):
                        self.bought = False
                    def next(self):
                        if not self.bought:
                            size = int(self.broker.getcash() / self.data.close[0])
                            self.buy(size=size)
                            self.bought = True
                            
                cerebro.addstrategy(BuyHoldStrategy)
                
            else:  # smart
                cerebro.addstrategy(SmartStrategy)
            
            # Configurar y ejecutar
            cerebro.broker.setcash(100000.0)
            cerebro.broker.setcommission(commission=0.001)
            
            inicial = cerebro.broker.getvalue()
            cerebro.run()
            final = cerebro.broker.getvalue()
            
            returns = (final - inicial) / inicial * 100
            results[name] = returns
            
            print(f"{name}: {returns:.2f}%")
            
        except Exception as e:
            print(f"Error testing {name}: {e}")
            results[name] = -100
    
    # Mostrar comparación
    print(f"\n=== COMPARACIÓN DE ESTRATEGIAS ===")
    sorted_results = sorted(results.items(), key=lambda x: x[1], reverse=True)
    
    for i, (name, returns) in enumerate(sorted_results, 1):
        print(f"{i}. {name}: {returns:.2f}%")
    
    best_strategy = sorted_results[0][0]
    print(f"\n🏆 Mejor estrategia: {best_strategy}")


if __name__ == "__main__":
    
    if MODE == "backtest":
        print("Modo: BACKTEST")
        run_backtest_analysis()
        print("\n" + "="*50)
        compare_strategies()
        
    elif MODE in ["paper", "live"]:
        print(f"Modo: {MODE.upper()}")
        run_live_paper_trading()
        
    else:
        print("❌ MODE debe ser: backtest, paper, o live")
        print("Ejemplo: MODE=backtest python trading_system.py")
    
    print("\n✅ Sistema híbrido completado")