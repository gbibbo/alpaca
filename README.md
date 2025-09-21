# Alpaca Trading System

Sistema completo de trading algorítmico usando Backtrader + Alpaca Markets para backtesting y ejecución en vivo.

## Características

- **Backtesting robusto** con datos históricos de 1-minuto
- **Paper trading** con ejecución de órdenes reales en simulación
- **Estrategias múltiples** con indicadores técnicos avanzados
- **Test de infraestructura** con estrategia random 50/50
- **Análisis completo** con Sharpe ratio, drawdown y métricas de performance

## Componentes

### Scripts principales

- `trading_complete.py` - Sistema completo con backtest 1-min y integración Alpaca
- `finrl_basic_agent.py` - Agente de trading con alpaca-py (funcional)
- `test_alpaca_connection.py` - Test de conexión con Alpaca API
- `trading_system.py` - Sistema híbrido Backtrader + Alpaca

### Estrategias implementadas

1. **RandomFlip50** - Test 50/50 para validar infraestructura
2. **SmartStrategy** - Estrategia basada en SMA, RSI, MACD con señales múltiples

## Instalación

1. **Crear entorno virtual**
```bash
conda create -n trading_bot python=3.11 -y
conda activate trading_bot
```

2. **Instalar dependencias**
```bash
pip install pandas numpy backtrader matplotlib scikit-learn
pip install yfinance python-dotenv alpaca-py
```

3. **Configurar credenciales de Alpaca**
```bash
cp .env.example .env
# Editar .env con tus API keys de Alpaca Paper Trading
```

## Configuración

Crear archivo `.env` con:
```env
APCA_API_KEY_ID=tu_api_key_aqui
APCA_API_SECRET_KEY=tu_secret_key_aqui
APCA_API_BASE_URL=https://paper-api.alpaca.markets
SYMBOL=AAPL
TIMEFRAME_MINUTES=1
RISK_PCT=0.20
```

## Uso

### Backtest con datos 1-minuto
```bash
MODE=backtest SYMBOL=AAPL python trading_complete.py
```

### Paper trading en vivo
```bash
MODE=paper SYMBOL=AAPL python finrl_basic_agent.py
```

### Test de conexión
```bash
python test_alpaca_connection.py
```

## Resultados de ejemplo

### Backtest 1-minuto (AAPL)
- **Smart Strategy**: -2.35% (134 trades ejecutados)
- **Random 50/50**: -51.51% (valida infraestructura)
- **Sharpe Ratio**: -0.036 (timeframe minutos)
- **Max Drawdown**: 2.39%

### Paper trading ejecutado
- Órdenes reales ejecutadas en simulación
- $35,000 invertidos de $200,000 disponibles
- Sistema funcionando con conexión Alpaca estable

## Estructura del proyecto

```
alpaca/
├── trading_complete.py      # Sistema completo según especificaciones
├── finrl_basic_agent.py     # Agente funcional con alpaca-py
├── test_alpaca_connection.py # Test de conectividad
├── trading_system.py        # Sistema híbrido
├── .env                     # Credenciales (NO incluido en repo)
├── .gitignore              # Archivos a ignorar
└── README.md               # Este archivo
```

## Tecnologías utilizadas

- **Backtrader**: Framework de backtesting y trading
- **Alpaca Markets**: Broker API para paper/live trading
- **pandas/numpy**: Manipulación de datos
- **yfinance**: Datos históricos gratuitos
- **scikit-learn**: Análisis de datos
- **alpaca-py**: Nueva API oficial de Alpaca

## Notas técnicas

### Limitaciones conocidas
- `alpaca-backtrader-api` tiene problemas de compatibilidad con Python 3.11 moderno
- El sistema usa un enfoque híbrido para evitar dependencias obsoletas
- Datos 1-minuto limitados en plan gratuito de Alpaca

### Rendimiento validado
El sistema ha ejecutado exitosamente:
- 134 trades en backtest 1-minuto
- Órdenes reales en paper trading
- Análisis completo de métricas
- Comparación de estrategias

## Licencia

MIT License - Uso libre para fines educativos y de investigación.

## Disclaimer

Este software es solo para fines educativos. El trading de acciones implica riesgo y puede resultar en pérdidas financieras. Use paper trading antes de cualquier implementación con dinero real.