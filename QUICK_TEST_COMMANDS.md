# Comandos Rápidos para Verificar Todo Funciona

## 🚀 Tests Rápidos (copiar y pegar)

```bash
cd /mnt/c/VS\ projects/alpaca

# O si ya estás en /mnt/c/VS projects/:
cd alpaca
```

---

## ✅ Test 1: Suite Completa de Regresión (RECOMENDADO)

```bash
# Ejecutar todos los tests automatizados
bash scripts/test_regression_epic6_7.sh
```

**Resultado esperado:**
```
SUCCESS: ALL REGRESSION TESTS PASSED
Existing functionality is intact
Epic 6 & 7 integration successful
```

---

## ✅ Test 2: Health Check Comprehensivo

```bash
# Tests de salud del sistema
python scripts/test_system_health.py
```

**Resultado esperado:**
```
✅ ALL HEALTH CHECKS PASSED
✅ System is healthy
✅ Epic 6 & 7 integration successful
```

---

## ✅ Test 3: Tests Individuales Rápidos

### 3.1 Importaciones Básicas
```bash
python -c "
from apps.risk_manager.main import EnhancedRiskManager
from apps.simulator.main import HistoricalSimulator
from apps.risk_manager.market_hours import MarketHoursValidator
from apps.simulator.persist import BacktestPersistence
print('✅ All imports OK')
"
```

### 3.2 Market Hours Status
```bash
python -c "
from apps.risk_manager.market_hours import MarketHoursValidator
v = MarketHoursValidator()
is_open, reason = v.validate_trading_hours()
print(f'Market Status: {reason}')
"
```

### 3.3 Holiday Detection
```bash
python -c "
from apps.risk_manager.market_hours import MarketCalendar
from datetime import datetime
cal = MarketCalendar()
holidays = [
    ('2024-12-25', 'Christmas'),
    ('2024-11-28', 'Thanksgiving'),
    ('2024-07-04', 'Independence Day'),
]
for date_str, name in holidays:
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    is_h, detected_name = cal.is_holiday(dt)
    print(f'{name:20} {\"✅\" if is_h else \"❌\"} {detected_name or \"N/A\"}')
"
```

### 3.4 Early Close Detection
```bash
python -c "
from apps.risk_manager.market_hours import MarketCalendar
from datetime import datetime
cal = MarketCalendar()
black_friday = datetime(2024, 11, 29)
is_early, reason = cal.is_early_close(black_friday)
print(f'Black Friday: {\"✅ Early close\" if is_early else \"❌ Not early close\"} - {reason}')
"
```

### 3.5 Persistence Test
```bash
python -c "
from apps.simulator.persist import BacktestPersistence
import tempfile, shutil
td = tempfile.mkdtemp()
p = BacktestPersistence('test', td)
p.save_bar({'symbol':'AAPL','timestamp':'2024-01-02T10:00:00','open':150,'high':151,'low':149.5,'close':150.5,'volume':1000000,'timeframe':'1Min'})
print(f'✅ Persistence OK - DB: {p.db_path.exists()}')
p.close()
shutil.rmtree(td)
"
```

### 3.6 Backward Compatibility
```bash
python -c "
from apps.simulator.main import HistoricalSimulator
from apps.risk_manager.main import RiskManager
sim = HistoricalSimulator()  # Sin persistencia
rm = RiskManager()  # Alias viejo
print(f'✅ Simulator (no persist): {sim.persistence is None}')
print(f'✅ RiskManager alias works: {rm is not None}')
"
```

### 3.7 Risk Manager Integration
```bash
python -c "
from apps.risk_manager.main import EnhancedRiskManager
rm = EnhancedRiskManager()
print(f'✅ Market validator type: {type(rm.market_validator).__name__}')
print(f'✅ Has deduplication: {rm.deduplication is not None}')
print(f'✅ Has rate limiters: {rm.order_rate_limiter is not None}')
"
```

---

## ✅ Test 4: Simulación End-to-End

### 4.1 Crear Datos de Prueba
```bash
mkdir -p test_data
cat > test_data/AAPL.csv << 'EOF'
timestamp,open,high,low,close,volume
2024-01-02T09:30:00,150.0,151.0,149.5,150.5,1000000
2024-01-02T09:31:00,150.5,151.5,150.0,151.0,1100000
2024-01-02T09:32:00,151.0,152.0,150.5,151.5,1200000
2024-01-02T09:33:00,151.5,152.5,151.0,152.0,1300000
2024-01-02T09:34:00,152.0,153.0,151.5,152.5,1400000
EOF
```

### 4.2 Simulación SIN Persistencia (Backward Compatibility)
```bash
python apps/simulator/main.py \
  --symbols AAPL \
  --start 2024-01-02 \
  --end 2024-01-02 \
  --csv test_data \
  --speed 100 \
  --no-delays \
  --seed 42
```

**Resultado esperado:**
```
🎉 Simulation completed successfully!
(SIN mensaje de persistencia)
```

### 4.3 Simulación CON Persistencia (Nueva Feature)
```bash
python apps/simulator/main.py \
  --symbols AAPL \
  --start 2024-01-02 \
  --end 2024-01-02 \
  --csv test_data \
  --speed 100 \
  --no-delays \
  --persist \
  --seed 42
```

**Resultado esperado:**
```
📁 Persistence enabled:
   Run ID: run_XXXXXXXX_XXXXXXXX_XXXXXXXX
   Output: out/run_XXXXXXXX_XXXXXXXX_XXXXXXXX
   Database: out/run_XXXXXXXX_XXXXXXXX_XXXXXXXX/backtest.db
🎉 Simulation completed successfully!
```

### 4.4 Verificar Output de Persistencia
```bash
# Listar archivos generados
find out -type f -name "*.db" -o -name "*.json" -o -name "*.csv" | head -10

# Ver summary
cat $(find out -name "summary.json" -type f | head -1) | python -m json.tool

# Ver registros en DB
DB=$(find out -name "backtest.db" -type f | head -1)
if [ -f "$DB" ]; then
  echo "Bars: $(sqlite3 "$DB" 'SELECT COUNT(*) FROM bars')"
  echo "Signals: $(sqlite3 "$DB" 'SELECT COUNT(*) FROM signals')"
  echo "Orders: $(sqlite3 "$DB" 'SELECT COUNT(*) FROM orders')"
fi
```

---

## ✅ Test 5: Reproducibilidad

```bash
# Run 1
python apps/simulator/main.py \
  --symbols AAPL --start 2024-01-02 --end 2024-01-02 \
  --csv test_data --speed 100 --no-delays \
  --persist --run-id repro_1 --seed 42 \
  > /tmp/run1.log 2>&1

# Run 2
python apps/simulator/main.py \
  --symbols AAPL --start 2024-01-02 --end 2024-01-02 \
  --csv test_data --speed 100 --no-delays \
  --persist --run-id repro_2 --seed 42 \
  > /tmp/run2.log 2>&1

# Comparar hashes
HASH1=$(grep "Results hash" /tmp/run1.log | awk '{print $NF}')
HASH2=$(grep "Results hash" /tmp/run2.log | awk '{print $NF}')
echo "Hash 1: $HASH1"
echo "Hash 2: $HASH2"
if [ "$HASH1" = "$HASH2" ]; then
  echo "✅ Reproducibilidad verificada"
else
  echo "❌ Hashes diferentes"
fi
```

---

## ✅ Test 6: Verificar Servicios Individuales

### Redis
```bash
redis-cli ping
# Esperado: PONG
```

### Bus de Mensajes
```bash
python -c "
from lib.bus import connect_bus, get_bus
assert connect_bus()
bus = get_bus()
print(f'✅ Bus connected: {bus.get_stats()}')
bus.disconnect()
"
```

### Settings
```bash
python -c "
from lib.settings import get_settings
s = get_settings()
print(f'✅ Symbols: {s.symbols_list}')
print(f'✅ Market TZ: {s.market_timezone}')
print(f'✅ Order limit: {s.max_orders_per_minute}/min')
"
```

---

## ✅ Test 7: Validación de Épicas Específicas

### Épica 6 - Market Hours
```bash
python scripts/validate_epic6_7.py 2>&1 | grep -A 20 "Epic 6"
```

### Épica 7 - Persistence
```bash
python scripts/validate_epic6_7.py 2>&1 | grep -A 20 "Epic 7"
```

---

## 🔍 Troubleshooting

### Si Redis no está disponible:
```bash
redis-server &
sleep 2
redis-cli ping
```

### Si hay problemas de imports:
```bash
# Verificar environment
conda activate trading_bot

# Verificar path
pwd  # Debe ser: /mnt/c/VS projects/alpaca

# Reinstalar dependencias si es necesario
pip install -r requirements.txt
```

### Si tests fallan por permisos:
```bash
chmod +x scripts/*.sh
```

---

## 📊 Resumen de Verificación

Después de ejecutar los tests, deberías ver:

- ✅ **Test 1 (Regresión):** ~27 tests pasados
- ✅ **Test 2 (Health):** ~13 tests pasados
- ✅ **Test 3 (Individuales):** Todos los checks pasan
- ✅ **Test 4 (E2E):** Simulación completa exitosa
- ✅ **Test 5 (Reproducibilidad):** Hashes idénticos
- ✅ **Test 6 (Servicios):** Todos conectan correctamente

---

## ✅ Checklist Final

Marca cada item después de verificarlo:

- [ ] Suite de regresión pasa (Test 1)
- [ ] Health check pasa (Test 2)
- [ ] Market hours detecta feriados (Test 3.3)
- [ ] Early close funciona (Test 3.4)
- [ ] Persistencia guarda datos (Test 3.5)
- [ ] Backward compatibility OK (Test 3.6)
- [ ] Simulación sin persist funciona (Test 4.2)
- [ ] Simulación con persist funciona (Test 4.3)
- [ ] Archivos generados existen (Test 4.4)
- [ ] Reproducibilidad verificada (Test 5)
- [ ] Redis funciona (Test 6)
- [ ] Épica 6 validada (Test 7)
- [ ] Épica 7 validada (Test 7)

---

## 🎯 Comando Todo-en-Uno

Para ejecutar todo de una vez:

```bash
cd /mnt/c/VS\ projects/alpaca

echo "=== Test 1: Regression Suite ==="
bash scripts/test_regression_epic6_7.sh

echo ""
echo "=== Test 2: System Health ==="
python scripts/test_system_health.py

echo ""
echo "=== Test 3: Epic 6 & 7 Validation ==="
python scripts/validate_epic6_7.py

echo ""
echo "✅ ALL TESTS COMPLETED"
```

---

**Si todos los tests pasan, el sistema está 100% funcional y listo para uso! 🎉**
