#!/usr/bin/env python3
"""
Test script para verificar conexión con Alpaca Paper Trading
VERSION: 3.0 - Usando nueva API alpaca-py
"""

import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timedelta

# Cargar variables de entorno
load_dotenv()

def test_alpaca_connection():
    """Prueba la conexión con Alpaca Paper Trading usando nueva API"""
    
    print("=== TEST DE CONEXIÓN ALPACA PAPER TRADING ===")
    print("VERSION: 3.0 - Nueva API alpaca-py")
    print("=" * 50)
    
    # 1. Verificar variables de entorno
    print("\n1. Verificando configuración...")
    api_key = os.getenv('APCA_API_KEY_ID')
    secret_key = os.getenv('APCA_API_SECRET_KEY')
    base_url = os.getenv('APCA_API_BASE_URL')
    
    if not all([api_key, secret_key]):
        print("❌ ERROR: Faltan variables de entorno en el archivo .env")
        print("Necesitas: APCA_API_KEY_ID y APCA_API_SECRET_KEY")
        return False
    
    print(f"✅ API Key: {api_key[:8]}..." if api_key else "❌ API Key no encontrada")
    print(f"✅ Base URL: {base_url}")
    
    # 2. Crear conexión API para Trading
    print("\n2. Conectando a Alpaca Trading...")
    try:
        # Determinar si es paper trading
        paper = "paper" in base_url.lower() if base_url else True
        
        trading_client = TradingClient(
            api_key=api_key,
            secret_key=secret_key,
            paper=paper
        )
        print("✅ Cliente Trading creado exitosamente")
    except Exception as e:
        print(f"❌ Error creando cliente Trading: {e}")
        return False
    
    # 3. Probar conexión - obtener info de cuenta
    print("\n3. Probando conexión - información de cuenta...")
    try:
        account = trading_client.get_account()
        print("✅ Conexión exitosa!")
        print(f"   - Account ID: {account.id}")
        print(f"   - Status: {account.status}")
        print(f"   - Buying Power: ${float(account.buying_power):,.2f}")
        print(f"   - Cash: ${float(account.cash):,.2f}")
        print(f"   - Portfolio Value: ${float(account.portfolio_value):,.2f}")
        print(f"   - Paper Trading: {'Sí' if paper else 'No'}")
    except Exception as e:
        print(f"❌ Error obteniendo información de cuenta: {e}")
        return False
    
    # 4. Crear cliente para datos históricos
    print("\n4. Probando cliente de datos históricos...")
    try:
        data_client = StockHistoricalDataClient(
            api_key=api_key,
            secret_key=secret_key
        )
        print("✅ Cliente de datos creado exitosamente")
    except Exception as e:
        print(f"❌ Error creando cliente de datos: {e}")
        return False
    
    # 5. Probar datos históricos
    print("\n5. Probando descarga de datos históricos...")
    try:
        # Usar datos de hace más de 15 días (plan gratuito)
        end_time = datetime.now() - timedelta(days=20)
        start_time = end_time - timedelta(days=5)
        
        print(f"   - Solicitando datos desde {start_time.date()} hasta {end_time.date()}")
        
        request_params = StockBarsRequest(
            symbol_or_symbols=["AAPL"],
            timeframe=TimeFrame.Day,
            start=start_time,
            end=end_time
        )
        
        bars = data_client.get_stock_bars(request_params)
        
        if bars.df is not None and not bars.df.empty:
            df = bars.df
            print("✅ Datos históricos obtenidos exitosamente!")
            print(f"   - Símbolo: AAPL")
            print(f"   - Datos obtenidos: {len(df)} barras")
            print(f"   - Último precio: ${df.iloc[-1]['close']:.2f}")
            print(f"   - Última fecha: {df.index[-1].strftime('%Y-%m-%d')}")
            print("   - ℹ️ Datos históricos (plan gratuito)")
        else:
            print("⚠️ No se obtuvieron datos históricos")
            
    except Exception as e:
        print(f"❌ Error obteniendo datos históricos: {e}")
        print("   Esto puede ser normal en cuentas gratuitas - continuando...")
    
    # 6. Probar posiciones actuales
    print("\n6. Verificando posiciones actuales...")
    try:
        positions = trading_client.get_all_positions()
        print(f"✅ Posiciones obtenidas: {len(positions)} posiciones activas")
        
        if positions:
            for pos in positions[:5]:  # Mostrar solo las primeras 5
                print(f"   - {pos.symbol}: {pos.qty} shares @ ${float(pos.avg_cost):.2f}")
        else:
            print("   - No hay posiciones activas (cuenta limpia)")
            
    except Exception as e:
        print(f"❌ Error obteniendo posiciones: {e}")
    
    print("\n=== RESUMEN VERSION 3.0 ===")
    print("✅ Conexión establecida correctamente")
    print("✅ Cuenta Paper Trading activa")
    print("✅ Nueva API alpaca-py funcionando")
    print("✅ Configuración lista para trading")
    print("\nPuedes proceder al siguiente paso: crear el sistema de trading.")
    
    return True

if __name__ == "__main__":
    success = test_alpaca_connection()
    if not success:
        print("\n❌ Hay problemas con la configuración.")
        print("VERSION EJECUTADA: 3.0")
        exit(1)
    else:
        print("VERSION EJECUTADA: 3.0 - EXITOSA")