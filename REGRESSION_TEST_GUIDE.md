# Guía de Tests de Regresión - Épica 6 & 7

## Objetivo

Verificar que las implementaciones de **Épica 6 (Market Hours)** y **Épica 7 (Persistence)** no rompieron ninguna funcionalidad existente del sistema de trading.

---

## Scripts de Verificación

Se han creado dos scripts principales:

### 1. `scripts/test_regression_epic6_7.sh` (Bash)
Script de tests rápidos en Bash

### 2. `scripts/test_system_health.py` (Python)
Tests comprehensivos de salud del sistema

---

## Ejecución de Tests

### Opción 1: Tests Rápidos (Bash)

```bash
cd /mnt/c/VS\ projects/alpaca

# O simplemente:
cd alpaca  # si ya estás en /mnt/c/VS projects/

# Ejecutar suite de regresión
bash scripts/test_regression_epic6_7.sh
```

**Tiempo estimado:** 2-3 minutos

**Tests incluidos:**
- ✅ Importaciones básicas (6 tests)
- ✅ Importaciones de servicios (5 tests)
- ✅ Funcionalidad de Risk Manager (7 tests)
- ✅ Funcionalidad de Simulator (4 tests)
- ✅ Bus y mensajería (3 tests)
- ✅ Backward compatibility (2 tests)

### Opción 2: Tests Comprehensivos (Python)

```bash
cd alpaca

# Ejecutar health check completo
python scripts/test_system_health.py
```

**Tiempo estimado:** 3-5 minutos

**Tests incluidos:**
- ✅ Todos los imports
- ✅ Risk Manager con market hours
- ✅ Risk Manager signal processing
- ✅ Backward compatibility
- ✅ Simulator con y sin persistencia
- ✅ Message bus
- ✅ Deduplication
- ✅ Settings
- ✅ Time utilities
- ✅ Metrics
- ✅ Data models
- ✅ Persistence module

---

## Resultados Esperados

### ✅ Tests Exitosos

Si todo funciona correctamente, verás:

```
============================================================
SUCCESS: ALL REGRESSION TESTS PASSED
Existing functionality is intact
Epic 6 & 7 integration successful
============================================================
```

O para el Python:

```
============================================================
✅ ALL HEALTH CHECKS PASSED
✅ System is healthy
✅ Epic 6 & 7 integration successful
============================================================
```

### ❌ Tests Fallidos

Si algún test falla, verás:

```
Test X: <nombre del test> ... FAIL
  Error log: /tmp/test_output_X.log
```

Para ver el error detallado:

```bash
cat /tmp/test_output_X.log
```

---

## Tests Manuales Adicionales

Si quieres hacer tests más específicos manualmente:

### Test 1: Market Hours Validation

```bash
# Verificar estado actual del mercado
python -c "
from apps.risk_manager.market_hours import MarketHoursValidator
validator = MarketHoursValidator()
is_open, reason = validator.validate_trading_hours()
print(f'Market Status: {reason}')
print(f'Is Open: {is_open}')
"
```

### Test 2: Holiday Detection

```bash
# Verificar que detecta Christmas Day
python -c "
from apps.risk_manager.market_hours import MarketCalendar
from datetime import datetime
cal = MarketCalendar()
is_holiday, name = cal.is_holiday(datetime(2024, 12, 25))
print(f'Christmas 2024: {name if is_holiday else \"Not a holiday\"}')
"
```

### Test 3: Persistence

```bash
# Test de persistencia básica
python -c "
from apps.simulator.persist import BacktestPersistence
import tempfile, shutil

td = tempfile.mkdtemp()
p = BacktestPersistence('manual_test', td)
p.save_bar({
    'symbol': 'AAPL',
    'timestamp': '2024-01-02T10:00:00',
    'open': 150, 'high': 151, 'low': 149.5, 'close': 150.5,
    'volume': 1000000, 'timeframe': '1Min'
})

summary = p.get_summary_stats()
print(f'Bars saved: {summary[\"bars_count\"]}')
p.close()
shutil.rmtree(td)
"
```

### Test 4: Backward Compatibility - Simulator

```bash
# Verificar que simulator funciona SIN persistencia
python -c "
from apps.simulator.main import HistoricalSimulator
sim = HistoricalSimulator(speed_multiplier=10.0)
print(f'Persistence: {sim.persistence}')  # Should be None
print(f'Speed: {sim.speed_multiplier}')  # Should be 10.0
print('✅ Backward compatibility OK' if sim.persistence is None else '❌ FAIL')
"
```

### Test 5: Risk Manager with Alpaca Clock API

```bash
# Verificar integración con Alpaca Clock API
python -c "
from apps.risk_manager.main import EnhancedRiskManager
rm = EnhancedRiskManager()
stats = rm.market_validator.get_stats()
print(f'Has Clock API: {stats[\"has_clock_api\"]}')
print(f'Market Status: {stats[\"status\"]}')
"
```

---

## Tests de Integración End-to-End

### Test E2E 1: Simulación Simple con Persistencia

```bash
# Crear datos de prueba
mkdir -p test_data
cat > test_data/AAPL.csv << 'EOF'
timestamp,open,high,low,close,volume
2024-01-02T09:30:00,150.0,151.0,149.5,150.5,1000000
2024-01-02T09:31:00,150.5,151.5,150.0,151.0,1100000
2024-01-02T09:32:00,151.0,152.0,150.5,151.5,1200000
EOF

# Ejecutar simulación
python apps/simulator/main.py \
  --symbols AAPL \
  --start 2024-01-02 \
  --end 2024-01-02 \
  --csv test_data \
  --speed 100 \
  --no-delays \
  --persist \
  --seed 42

# Verificar output
ls -la out/run_*/
cat out/run_*/summary.json
```

### Test E2E 2: Risk Manager Rechaza Señales en Feriado

```python
# Crear script de test
python << 'EOF'
from apps.risk_manager.main import EnhancedRiskManager
from lib.models import Signal, SignalSide
from decimal import Decimal
from datetime import datetime
import pytz
import uuid

rm = EnhancedRiskManager()

# Signal en Christmas Day
et = pytz.timezone('US/Eastern')
christmas = et.localize(datetime(2024, 12, 25, 10, 0))

signal = Signal(
    signal_id=uuid.uuid4(),
    symbol="AAPL",
    timestamp=christmas,
    side=SignalSide.BUY,
    confidence=Decimal("0.85"),
    price=Decimal("150.0"),
    source="test"
)

# Verificar que el signal se valida contra market hours
is_open, reason = rm.market_validator.calendar.validate_trading_time(christmas)
print(f"Christmas validation: {is_open} - {reason}")
print("✅ PASS: Holiday correctly rejected" if not is_open else "❌ FAIL: Should reject holiday")
EOF
```

---

## Troubleshooting

### Problema: Tests fallan por Redis no disponible

**Solución:**
```bash
# Verificar que Redis está corriendo
redis-cli ping
# Si no responde, iniciar Redis
redis-server &
```

### Problema: Import errors

**Solución:**
```bash
# Asegurarse de estar en el environment correcto
conda activate trading_bot  # o tu environment

# Verificar que estás en el directorio correcto
pwd  # Debe mostrar: /mnt/c/VS projects/alpaca
```

### Problema: Permission denied en scripts .sh

**Solución:**
```bash
chmod +x scripts/*.sh
```

### Problema: Tests pasan pero market hours siempre rechaza

**Explicación:** Esto es **normal** si los tests se ejecutan fuera del horario de mercado (9:30 AM - 4:00 PM ET, Lunes-Viernes). El validador está funcionando correctamente.

Para verificar:
```bash
python -c "
from apps.risk_manager.market_hours import MarketHoursValidator
validator = MarketHoursValidator()
stats = validator.get_stats()
print(f'Current time (ET): {stats[\"current_time\"]}')
print(f'Status: {stats[\"status\"]}')
"
```

---

## Checklist de Verificación Manual

Después de ejecutar los tests automatizados, verifica manualmente:

### Épica 6 - Market Hours
- [ ] Risk Manager se inicializa sin errores
- [ ] Market hours validator está presente
- [ ] Detecta feriados correctamente (Christmas, Thanksgiving, etc.)
- [ ] Detecta early close days (Black Friday, Christmas Eve)
- [ ] Se conecta a Alpaca Clock API (si hay credenciales)
- [ ] Fallback a cálculo local funciona

### Épica 7 - Persistence
- [ ] Simulator funciona SIN --persist (backward compatibility)
- [ ] Simulator funciona CON --persist (nueva feature)
- [ ] Se crea estructura out/<run_id>/
- [ ] Se crea base de datos SQLite
- [ ] Se exporta a CSV
- [ ] Se genera summary.json
- [ ] Hash de reproducibilidad funciona

### Backward Compatibility
- [ ] Tests existentes siguen pasando
- [ ] Simulator sin persistencia funciona igual que antes
- [ ] Risk Manager procesa señales normalmente
- [ ] Alias RiskManager sigue funcionando
- [ ] Bus de mensajes funciona igual
- [ ] Métricas siguen funcionando

### Integración
- [ ] Todos los servicios inician sin errores
- [ ] Flujo end-to-end funciona (bars → signals → orders)
- [ ] Métricas de Prometheus accesibles
- [ ] No hay regresiones en performance

---

## Métricas de Éxito

Para considerar que todo funciona correctamente:

1. **Tests Automatizados:** 100% de tests pasan
2. **Imports:** Todos los módulos se importan sin errores
3. **Servicios:** Todos los servicios inician correctamente
4. **Funcionalidad:** Épicas 6 y 7 funcionan según especificación
5. **Backward Compatibility:** Código existente funciona sin cambios
6. **Performance:** No hay degradación notable en tiempos de respuesta

---

## Reportar Problemas

Si encuentras algún problema:

1. Ejecutar tests en modo verbose:
   ```bash
   python scripts/test_system_health.py 2>&1 | tee test_results.log
   ```

2. Capturar logs de servicios:
   ```bash
   python apps/risk_manager/main.py > rm.log 2>&1 &
   # Revisar rm.log para errores
   ```

3. Incluir en el reporte:
   - Output de tests
   - Logs relevantes
   - Versión de Python y dependencias
   - Environment (trading_bot, etc.)

---

## Siguiente Paso

Una vez que todos los tests pasen, el sistema está listo para:

1. **Testing en staging:** Ejecutar con datos reales en paper trading
2. **Monitoreo:** Verificar métricas de Prometheus/Grafana
3. **Validación funcional:** Probar flujos end-to-end completos
4. **Performance testing:** Stress tests con alta carga

---

## Resumen de Comandos

```bash
# Tests rápidos (2-3 min)
bash scripts/test_regression_epic6_7.sh

# Tests comprehensivos (3-5 min)
python scripts/test_system_health.py

# Test manual - Market hours
python -c "from apps.risk_manager.market_hours import MarketHoursValidator; v = MarketHoursValidator(); print(v.validate_trading_hours())"

# Test manual - Persistence
python -c "from apps.simulator.persist import BacktestPersistence; import tempfile, shutil; td = tempfile.mkdtemp(); p = BacktestPersistence('t', td); print(p.run_dir); p.close(); shutil.rmtree(td)"

# Test E2E - Simulación
python apps/simulator/main.py --symbols AAPL --start 2024-01-02 --end 2024-01-02 --csv test_data --speed 100 --no-delays --persist --seed 42
```

---

**Fecha:** Octubre 2024
**Versión:** 1.0
**Autor:** Sistema de Trading Algorítmico
