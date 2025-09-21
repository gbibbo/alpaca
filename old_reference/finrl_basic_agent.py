#!/usr/bin/env python3
"""
FinRL + Alpaca: Agente básico de trading
VERSION: 2.0 - Usando nueva API alpaca-py
"""

import os
import warnings
warnings.filterwarnings('ignore')

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dotenv import load_dotenv
import yfinance as yf

# Nueva API de Alpaca
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

print("=== FINRL + ALPACA TRADING AGENT ===")
print("VERSION: 2.0 - Nueva API alpaca-py")
print("=" * 45)

# Cargar configuración
load_dotenv()

class SimpleAlpacaTrader:
    """
    VERSION 2.0: Usando nueva API alpaca-py (más estable)
    """
    
    def __init__(self):
        # Configuración Alpaca
        api_key = os.getenv('APCA_API_KEY_ID')
        secret_key = os.getenv('APCA_API_SECRET_KEY')
        base_url = os.getenv('APCA_API_BASE_URL', 'https://paper-api.alpaca.markets')
        
        # Determinar si es paper trading
        self.paper = "paper" in base_url.lower()
        
        # Clientes de Alpaca
        self.trading_client = TradingClient(
            api_key=api_key,
            secret_key=secret_key,
            paper=self.paper
        )
        
        self.data_client = StockHistoricalDataClient(
            api_key=api_key,
            secret_key=secret_key
        )
        
        # Configuración simple
        self.tickers = ['AAPL', 'MSFT', 'GOOGL', 'TSLA', 'NVDA']
        self.data = None
        
        print(f"Tickers seleccionados: {self.tickers}")
        print(f"Modo: {'Paper Trading' if self.paper else 'Live Trading'}")
        
    def verify_alpaca_connection(self):
        """Verificar conexión con Alpaca"""
        print("\n1. Verificando conexión con Alpaca...")
        try:
            account = self.trading_client.get_account()
            print(f"   Cuenta activa: ${float(account.buying_power):,.2f} buying power")
            print(f"   Cash: ${float(account.cash):,.2f}")
            print(f"   Portfolio: ${float(account.portfolio_value):,.2f}")
            print(f"   Status: {account.status}")
            return True
        except Exception as e:
            print(f"   Error de conexión: {e}")
            return False
    
    def download_data_yfinance(self):
        """Descargar datos usando yfinance (más confiable para históricos)"""
        print("\n2. Descargando datos con yfinance...")
        
        try:
            # Período de datos
            end_date = datetime.now()
            start_date = end_date - timedelta(days=365)  # 1 año
            
            print(f"   Período: {start_date.strftime('%Y-%m-%d')} a {end_date.strftime('%Y-%m-%d')}")
            
            all_data = []
            
            for ticker in self.tickers:
                print(f"   Descargando {ticker}...")
                
                try:
                    # Descargar datos UN TICKER A LA VEZ (evita MultiIndex)
                    stock = yf.download(ticker, start=start_date, end=end_date, progress=False)
                    
                    if not stock.empty and len(stock) > 50:  # Suficientes datos
                        # Resetear index para obtener Date como columna
                        stock_data = stock.reset_index()
                        
                        # VERIFICAR Y CORREGIR MULTIINDEX
                        if isinstance(stock_data.columns, pd.MultiIndex):
                            # Aplanar MultiIndex tomando el nivel 0
                            stock_data.columns = [col[0] if isinstance(col, tuple) else col for col in stock_data.columns]
                        
                        # Asegurar nombres estándar de columnas
                        column_mapping = {}
                        for col in stock_data.columns:
                            col_str = str(col).lower()
                            if 'date' in col_str or col == 'Date':
                                column_mapping[col] = 'date'
                            elif 'open' in col_str:
                                column_mapping[col] = 'open'
                            elif 'high' in col_str:
                                column_mapping[col] = 'high'
                            elif 'low' in col_str:
                                column_mapping[col] = 'low'
                            elif 'close' in col_str:
                                column_mapping[col] = 'close'
                            elif 'volume' in col_str:
                                column_mapping[col] = 'volume'
                        
                        stock_data = stock_data.rename(columns=column_mapping)
                        
                        # Añadir ticker
                        stock_data['tic'] = ticker
                        
                        # Verificar que tenemos las columnas básicas
                        required_cols = ['date', 'close']
                        if all(col in stock_data.columns for col in required_cols):
                            # Seleccionar solo columnas que existen
                            available_cols = ['date', 'tic']
                            for col in ['open', 'high', 'low', 'close', 'volume']:
                                if col in stock_data.columns:
                                    available_cols.append(col)
                            
                            stock_data = stock_data[available_cols]
                            all_data.append(stock_data)
                            print(f"     {ticker}: {len(stock_data)} filas descargadas")
                            print(f"     Columnas: {list(stock_data.columns)}")
                        else:
                            print(f"     {ticker}: Columnas requeridas faltantes")
                    else:
                        print(f"     {ticker}: Sin datos suficientes")
                        
                except Exception as e:
                    print(f"     {ticker}: Error - {e}")
                    continue
            
            if all_data:
                # Concatenar todos los datos
                self.data = pd.concat(all_data, ignore_index=True)
                self.data = self.data.sort_values(['date', 'tic']).reset_index(drop=True)
                
                print(f"   ✅ DATOS DESCARGADOS:")
                print(f"   - Total filas: {len(self.data)}")
                print(f"   - Tickers: {sorted(self.data['tic'].unique())}")
                print(f"   - Fechas: {self.data['date'].min()} a {self.data['date'].max()}")
                
                return True
            else:
                print("   ❌ No se pudieron descargar datos")
                return False
                
        except Exception as e:
            print(f"   ❌ Error descargando datos: {e}")
            return False
    
    def calculate_indicators(self):
        """Calcular indicadores técnicos"""
        print("\n3. Calculando indicadores técnicos...")
        
        try:
            processed_data = []
            
            for ticker in self.tickers:
                print(f"   Procesando {ticker}...")
                
                # Filtrar datos del ticker
                ticker_data = self.data[self.data['tic'] == ticker].copy()
                ticker_data = ticker_data.sort_values('date').reset_index(drop=True)
                
                if len(ticker_data) > 50:
                    # ASEGURAR QUE CLOSE ES UNA SERIE, NO DATAFRAME
                    close_col = ticker_data['close']
                    
                    # Verificar tipo y convertir si es necesario
                    if isinstance(close_col, pd.DataFrame):
                        close = close_col.iloc[:, 0]  # Tomar primera columna
                        print(f"     {ticker}: Corrigiendo DataFrame a Series")
                    else:
                        close = close_col
                    
                    # Verificar que close es numérico
                    close = pd.to_numeric(close, errors='coerce')
                    
                    print(f"     {ticker}: Close type={type(close)}, shape={close.shape}")
                    
                    # Calcular indicadores uno por uno con manejo de errores
                    try:
                        # SMA
                        sma_20 = close.rolling(window=20, min_periods=20).mean()
                        sma_50 = close.rolling(window=50, min_periods=50).mean()
                        ticker_data.loc[:, 'sma_20'] = sma_20
                        ticker_data.loc[:, 'sma_50'] = sma_50
                        
                        # RSI
                        delta = close.diff()
                        gain = delta.where(delta > 0, 0).rolling(window=14, min_periods=14).mean()
                        loss = (-delta.where(delta < 0, 0)).rolling(window=14, min_periods=14).mean()
                        rs = gain / loss
                        rsi = 100 - (100 / (1 + rs))
                        ticker_data.loc[:, 'rsi'] = rsi
                        
                        # MACD
                        ema_12 = close.ewm(span=12, min_periods=12).mean()
                        ema_26 = close.ewm(span=26, min_periods=26).mean()
                        macd = ema_12 - ema_26
                        macd_signal = macd.ewm(span=9, min_periods=9).mean()
                        ticker_data.loc[:, 'macd'] = macd
                        ticker_data.loc[:, 'macd_signal'] = macd_signal
                        
                        # Volatilidad
                        returns = close.pct_change()
                        volatility = returns.rolling(window=20, min_periods=20).std()
                        ticker_data.loc[:, 'volatility'] = volatility
                        
                        print(f"     {ticker}: Indicadores calculados exitosamente")
                        
                    except Exception as calc_error:
                        print(f"     {ticker}: Error en cálculo específico: {calc_error}")
                        continue
                    
                    # Solo datos válidos (quitar NaN)
                    valid_data = ticker_data.dropna(subset=['close', 'sma_20', 'rsi'])
                    
                    if len(valid_data) > 0:
                        processed_data.append(valid_data)
                        print(f"     {ticker}: {len(valid_data)} filas con indicadores válidos")
                        
                        # Mostrar último estado
                        try:
                            last = valid_data.iloc[-1]
                            close_val = float(last['close'])
                            sma20_val = float(last['sma_20'])
                            rsi_val = float(last['rsi'])
                            print(f"     Último: Close=${close_val:.2f}, SMA20=${sma20_val:.2f}, RSI={rsi_val:.1f}")
                        except:
                            print(f"     {ticker}: Datos calculados pero error en display")
                    else:
                        print(f"     {ticker}: Sin datos válidos después de indicadores")
                else:
                    print(f"     {ticker}: Datos insuficientes ({len(ticker_data)} filas)")
            
            if processed_data:
                self.data = pd.concat(processed_data, ignore_index=True)
                self.data = self.data.sort_values(['date', 'tic']).reset_index(drop=True)
                
                print(f"   ✅ INDICADORES CALCULADOS:")
                print(f"   - Datos finales: {len(self.data)} filas")
                print(f"   - Columnas disponibles: {list(self.data.columns)}")
                
                return True
            else:
                print("   ❌ No se procesaron indicadores para ningún ticker")
                return False
                
        except Exception as e:
            print(f"   ❌ Error general calculando indicadores: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def create_trading_signals(self):
        """Crear señales de trading mejoradas"""
        print("\n4. Generando señales de trading...")
        
        try:
            signals = []
            
            # Obtener datos más recientes por ticker
            latest_data = self.data.groupby('tic').tail(1)
            
            for _, row in latest_data.iterrows():
                ticker = row['tic']
                close = row['close']
                sma_20 = row['sma_20']
                sma_50 = row['sma_50']
                rsi = row['rsi']
                macd = row['macd']
                macd_signal = row['macd_signal']
                volatility = row['volatility']
                
                # Estrategia multi-indicador
                signal = "HOLD"
                reason = "Condiciones neutras"
                confidence = 0
                
                if pd.notna(sma_20) and pd.notna(sma_50) and pd.notna(rsi):
                    buy_signals = 0
                    sell_signals = 0
                    
                    # Señal 1: Tendencia (SMA)
                    if close > sma_20 > sma_50:
                        buy_signals += 1
                    elif close < sma_20 < sma_50:
                        sell_signals += 1
                    
                    # Señal 2: RSI
                    if rsi < 30:  # Sobrevendido
                        buy_signals += 1
                    elif rsi > 70:  # Sobrecomprado
                        sell_signals += 1
                    
                    # Señal 3: MACD
                    if pd.notna(macd) and pd.notna(macd_signal):
                        if macd > macd_signal and macd > 0:
                            buy_signals += 1
                        elif macd < macd_signal and macd < 0:
                            sell_signals += 1
                    
                    # Decisión final
                    if buy_signals >= 2 and volatility < 0.05:  # Baja volatilidad
                        signal = "BUY"
                        reason = f"Señales alcistas: {buy_signals}/3"
                        confidence = buy_signals * 33
                    elif sell_signals >= 2:
                        signal = "SELL"
                        reason = f"Señales bajistas: {sell_signals}/3"
                        confidence = sell_signals * 33
                
                signals.append({
                    'ticker': ticker,
                    'signal': signal,
                    'reason': reason,
                    'confidence': confidence,
                    'price': close,
                    'sma_20': sma_20,
                    'rsi': rsi,
                    'volatility': volatility * 100  # En porcentaje
                })
            
            print("   📊 SEÑALES GENERADAS:")
            for sig in signals:
                print(f"     {sig['ticker']}: {sig['signal']} ({sig['confidence']}%) @ ${sig['price']:.2f}")
                print(f"       {sig['reason']} | RSI: {sig['rsi']:.1f} | Vol: {sig['volatility']:.1f}%")
            
            return signals
            
        except Exception as e:
            print(f"   ❌ Error generando señales: {e}")
            return []
    
    def execute_paper_trades(self, signals):
        """Ejecutar trades en paper trading"""
        print("\n5. Ejecutando trades (Paper Trading)...")
        
        try:
            account = self.trading_client.get_account()
            buying_power = float(account.buying_power)
            print(f"   💰 Buying power: ${buying_power:,.2f}")
            
            executed_orders = []
            
            for signal in signals:
                if signal['signal'] in ['BUY', 'SELL'] and signal['confidence'] >= 66:
                    
                    ticker = signal['ticker']
                    price = signal['price']
                    
                    try:
                        if signal['signal'] == 'BUY':
                            # Calcular cantidad (máximo $5000 por trade)
                            max_investment = min(5000, buying_power * 0.2)  # 20% del capital
                            quantity = int(max_investment / price)
                            
                            if quantity > 0 and max_investment <= buying_power:
                                # Crear orden de compra
                                market_order_data = MarketOrderRequest(
                                    symbol=ticker,
                                    qty=quantity,
                                    side=OrderSide.BUY,
                                    time_in_force=TimeInForce.DAY
                                )
                                
                                # Ejecutar orden (¡REAL en paper trading!)
                                order = self.trading_client.submit_order(market_order_data)
                                
                                executed_orders.append({
                                    'order_id': order.id,
                                    'symbol': ticker,
                                    'side': 'BUY',
                                    'qty': quantity,
                                    'status': order.status
                                })
                                
                                print(f"   ✅ COMPRA EJECUTADA: {quantity} {ticker} @ ${price:.2f}")
                                print(f"      Order ID: {order.id}")
                                buying_power -= quantity * price
                            else:
                                print(f"   ⚠️ SKIP {ticker}: Fondos insuficientes o cantidad=0")
                        
                        elif signal['signal'] == 'SELL':
                            # Verificar si tenemos posición
                            try:
                                position = self.trading_client.get_open_position(ticker)
                                quantity = int(float(position.qty))
                                
                                if quantity > 0:
                                    # Crear orden de venta
                                    market_order_data = MarketOrderRequest(
                                        symbol=ticker,
                                        qty=quantity,
                                        side=OrderSide.SELL,
                                        time_in_force=TimeInForce.DAY
                                    )
                                    
                                    # Ejecutar orden
                                    order = self.trading_client.submit_order(market_order_data)
                                    
                                    executed_orders.append({
                                        'order_id': order.id,
                                        'symbol': ticker,
                                        'side': 'SELL',
                                        'qty': quantity,
                                        'status': order.status
                                    })
                                    
                                    print(f"   ✅ VENTA EJECUTADA: {quantity} {ticker} @ ${price:.2f}")
                                    print(f"      Order ID: {order.id}")
                                else:
                                    print(f"   ⚠️ SKIP {ticker}: Sin posición para vender")
                            except:
                                print(f"   ⚠️ SKIP {ticker}: Sin posición para vender")
                    
                    except Exception as e:
                        print(f"   ❌ Error ejecutando {signal['signal']} {ticker}: {e}")
                        continue
                
                else:
                    print(f"   ⏸️ HOLD {signal['ticker']}: Baja confianza ({signal['confidence']}%)")
            
            print(f"\n   📋 RESUMEN DE EJECUCIÓN:")
            print(f"   - Órdenes ejecutadas: {len(executed_orders)}")
            print(f"   - Buying power restante: ${buying_power:,.2f}")
            
            if executed_orders:
                print("   📝 Órdenes:")
                for order in executed_orders:
                    print(f"     {order['side']} {order['qty']} {order['symbol']} - Status: {order['status']}")
            
            return executed_orders
            
        except Exception as e:
            print(f"   ❌ Error ejecutando trades: {e}")
            return []
    
    def run_trading_pipeline(self):
        """Ejecutar pipeline completo de trading"""
        print(f"\n🚀 EJECUTANDO PIPELINE VERSION 2.0...")
        
        # 1. Verificar conexión
        if not self.verify_alpaca_connection():
            return False
        
        # 2. Descargar datos
        if not self.download_data_yfinance():
            return False
        
        # 3. Calcular indicadores
        if not self.calculate_indicators():
            return False
        
        # 4. Generar señales
        signals = self.create_trading_signals()
        if not signals:
            return False
        
        # 5. Ejecutar trades
        orders = self.execute_paper_trades(signals)
        
        print("\n🎯 === RESUMEN FINAL VERSION 2.0 ===")
        print("   ✅ Pipeline ejecutado completamente")
        print("   ✅ Datos históricos procesados")
        print("   ✅ Indicadores técnicos calculados")
        print("   ✅ Señales de trading generadas")
        print("   ✅ Órdenes ejecutadas en paper trading")
        print(f"   📊 Total órdenes: {len(orders)}")
        print("\n🔄 Para ejecutar en vivo, programa este script cada X minutos")
        print("💡 Próximos pasos: Backtesting y optimización de estrategia")
        
        return True

if __name__ == "__main__":
    print("🎬 INICIANDO TRADING AGENT VERSION 2.0")
    trader = SimpleAlpacaTrader()
    success = trader.run_trading_pipeline()
    
    if success:
        print(f"\n🎉 VERSION 2.0 COMPLETADA EXITOSAMENTE")
        print("¡Sistema de trading algorítmico funcional con órdenes reales!")
    else:
        print(f"\n❌ VERSION 2.0 FALLÓ - revisar logs arriba")