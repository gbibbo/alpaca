# 🧪 Comandos de Validación - Épicas 4 y 5

**IMPORTANTE:** Estos comandos están listos para copiar y pegar directo en tu terminal Linux/WSL.

---

## 🚀 Opción 1: Script de Validación Simple (RECOMENDADO)

Este es el método más rápido y simple. No requiere pytest.

```bash
cd /mnt/c/VS\ projects/alpaca && python scripts/validate_epic4_5_simple.py
```

**Salida esperada:**
- ✅ Todas las validaciones pasan con colores verde/azul
- Exit code 0 si todo OK
- Resumen al final mostrando:
  - Epic 4 (Idempotencia): COMPLETA
  - Epic 5 (FSM): COMPLETA

---

## 🧪 Opción 2: Tests Unitarios con Pytest

### Test Epic 4 (Idempotencia)

```bash
cd /mnt/c/VS\ projects/alpaca && pytest tests/test_epic4_idempotency.py -v
```

**Tests esperados (6 tests):**
- ✅ test_generate_client_order_id_same_input
- ✅ test_generate_client_order_id_different_symbol
- ✅ test_client_order_id_max_length
- ✅ test_duplicate_order_blocked_by_client_id
- ✅ test_429_retry_uses_same_client_order_id
- ✅ test_duplicate_order_metric_increments
- ✅ test_429_retry_metric_increments

### Test Epic 5 (FSM)

```bash
cd /mnt/c/VS\ projects/alpaca && pytest tests/test_epic5_order_fsm.py -v
```

**Tests esperados (20+ tests):**
- ✅ test_all_states_defined
- ✅ test_terminal_states
- ✅ test_valid_transition_new_to_submitted
- ✅ test_valid_transition_submitted_to_accepted
- ✅ test_valid_transition_accepted_to_partial
- ✅ test_valid_transition_partial_to_filled
- ✅ test_invalid_transition_new_to_filled
- ✅ test_new_timeout_detection
- ✅ test_partial_fill_timeout
- ✅ test_filled_order_no_timeout
- ✅ Y más...

### Ambos Tests Juntos

```bash
cd /mnt/c/VS\ projects/alpaca && pytest tests/test_epic4_idempotency.py tests/test_epic5_order_fsm.py -v
```

---

## ⚡ Opción 3: Validación Ultra-Rápida (Una Línea)

### Epic 4: client_order_id Determinista

```bash
cd /mnt/c/VS\ projects/alpaca && python -c "import sys; sys.path.insert(0, '.'); from lib.models import OrderIntent, SignalSide; from decimal import Decimal; o = OrderIntent(symbol='GOOGL', side=SignalSide.BUY, quantity=Decimal('100'), client_order_id='test', signal_source='test', price=Decimal('150')); id1 = o.generate_client_order_id(); id2 = o.generate_client_order_id(); print(f'Epic 4 - client_order_id determinista: {\"✅ OK\" if (id1 == id2 and id1.startswith(\"risk_\") and len(id1) <= 50) else \"❌ FAIL\"}')"
```

**Salida esperada:**
```
Epic 4 - client_order_id determinista: ✅ OK
```

### Epic 5: FSM Básico

```bash
cd /mnt/c/VS\ projects/alpaca && python -c "import sys; sys.path.insert(0, '.'); from lib.order_fsm import OrderFSM, OrderState, OrderEvent; from lib.models import OrderIntent, SignalSide; from decimal import Decimal; o = OrderIntent(symbol='TSLA', side=SignalSide.BUY, quantity=Decimal('50'), client_order_id='test', signal_source='test', price=Decimal('700')); fsm = OrderFSM(o, 'broker_123'); s1 = fsm.current_state == OrderState.NEW; fsm.transition(OrderEvent.SUBMIT); s2 = fsm.current_state == OrderState.SUBMITTED; fsm.transition(OrderEvent.ACCEPT); s3 = fsm.current_state == OrderState.ACCEPTED; print(f'Epic 5 - FSM transiciones: {\"✅ OK\" if (s1 and s2 and s3) else \"❌ FAIL\"}')"
```

**Salida esperada:**
```
Epic 5 - FSM transiciones: ✅ OK
```

---

## 🔍 Opción 4: Verificación de Archivos Modificados

### Verificar Epic 4 - client_order_id

```bash
cd /mnt/c/VS\ projects/alpaca && grep -n "def generate_client_order_id" lib/models.py
```

**Salida esperada:**
```
177:    def generate_client_order_id(self, strategy_source: str = None) -> str:
```

### Verificar Epic 4 - Métricas

```bash
cd /mnt/c/VS\ projects/alpaca && grep -n "DUPLICATE_ORDER_BLOCKED_BY_CLIENT_ID\|BROKER_429_RETRIES" lib/metrics_helpers.py
```

**Salida esperada:**
```
199:DUPLICATE_ORDER_BLOCKED_BY_CLIENT_ID = Counter(
206:BROKER_429_RETRIES = Counter(
```

### Verificar Epic 4 - Verificación de orden existente

```bash
cd /mnt/c/VS\ projects/alpaca && grep -n "Epic 4: Check if order already exists" apps/executor/main.py
```

**Salida esperada:**
```
456:                # Epic 4: Check if order already exists by client_order_id (idempotency)
```

### Verificar Epic 5 - Archivo FSM existe

```bash
cd /mnt/c/VS\ projects/alpaca && ls -lh lib/order_fsm.py
```

**Salida esperada:**
```
-rw-r--r-- 1 user user 15K Jan  5 12:00 lib/order_fsm.py
```

### Verificar Epic 5 - Integración FSM en OrderTracker

```bash
cd /mnt/c/VS\ projects/alpaca && grep -n "order_fsms: Dict\|from lib.order_fsm import" apps/executor/main.py
```

**Salida esperada:**
```
141:        from lib.order_fsm import OrderFSM
142:        self.order_fsms: Dict[str, OrderFSM] = {}
154:        from lib.order_fsm import create_fsm_from_order_intent
189:            from lib.order_fsm import map_alpaca_status_to_event
```

### Verificar Epic 5 - Timeouts y Cancelación

```bash
cd /mnt/c/VS\ projects/alpaca && grep -n "Epic 5.*timeout\|async def check_timeouts" apps/executor/main.py
```

**Salida esperada:**
```
251:    async def check_timeouts(self) -> List[str]:
693:                # Epic 5: Check for order timeouts FIRST
```

---

## 📊 Opción 5: Validación Interactiva Paso a Paso

Si quieres ver cada validación paso a paso en Python interactivo:

```bash
cd /mnt/c/VS\ projects/alpaca && python
```

Luego copia y pega CADA BLOQUE uno por uno:

### Bloque 1: Importar todo

```python
import sys
sys.path.insert(0, '.')
from lib.models import OrderIntent, SignalSide, OrderType
from lib.order_fsm import OrderFSM, OrderState, OrderEvent, create_fsm_from_order_intent
from lib.metrics_helpers import ExecutorMetrics
from decimal import Decimal
```

### Bloque 2: Test client_order_id

```python
order = OrderIntent(symbol="GOOGL", side=SignalSide.BUY, quantity=Decimal("100"), client_order_id="temp", signal_source="test", price=Decimal("150"))
id1 = order.generate_client_order_id()
id2 = order.generate_client_order_id()
print(f"ID 1: {id1}")
print(f"ID 2: {id2}")
print(f"✅ Iguales: {id1 == id2}")
print(f"✅ Formato OK: {id1.startswith('risk_')}")
print(f"✅ Longitud OK: {len(id1) <= 50}")
```

### Bloque 3: Test FSM

```python
fsm = OrderFSM(order, "broker_123")
print(f"Estado inicial: {fsm.current_state}")
fsm.transition(OrderEvent.SUBMIT)
print(f"Después de SUBMIT: {fsm.current_state}")
fsm.transition(OrderEvent.ACCEPT)
print(f"Después de ACCEPT: {fsm.current_state}")
fsm.transition(OrderEvent.PARTIAL_FILL, fill_quantity=Decimal("50"), fill_price=Decimal("150"))
print(f"Después de PARTIAL_FILL: {fsm.current_state}")
print(f"Llenado: {fsm.filled_quantity} / {order.quantity}")
print(f"Porcentaje: {fsm.get_fill_percentage():.1%}")
```

### Bloque 4: Salir

```python
exit()
```

---

## 🎯 Checklist de Verificación

Después de ejecutar los comandos, verifica:

### Epic 4 ✅
- [ ] Script de validación muestra "Epic 4: ✅ COMPLETA"
- [ ] Pytest Epic 4 muestra 6-7 tests pasando
- [ ] `grep` encuentra `generate_client_order_id` en línea 177
- [ ] `grep` encuentra métricas Epic 4
- [ ] Validación rápida muestra "✅ OK"

### Epic 5 ✅
- [ ] Script de validación muestra "Epic 5: ✅ COMPLETA"
- [ ] Pytest Epic 5 muestra 20+ tests pasando
- [ ] `ls` muestra `lib/order_fsm.py` existe
- [ ] `grep` encuentra `order_fsms: Dict`
- [ ] `grep` encuentra `check_timeouts`
- [ ] Validación rápida muestra "✅ OK"

---

## 🚨 Si Algo Falla

### Error: "No module named 'lib'"

```bash
# Asegúrate de estar en el directorio correcto
cd /mnt/c/VS\ projects/alpaca
pwd  # Debe mostrar: /mnt/c/VS projects/alpaca
```

### Error: "pytest: command not found"

```bash
# Instalar pytest
pip install pytest pytest-asyncio
```

### Tests fallan con errores de import

```bash
# Verificar que los tests tienen sys.path.insert
head -10 tests/test_epic4_idempotency.py
head -10 tests/test_epic5_order_fsm.py
# Ambos deben tener: sys.path.insert(0, str(Path(__file__).parent.parent))
```

### Ver logs detallados de tests

```bash
cd /mnt/c/VS\ projects/alpaca && pytest tests/test_epic4_idempotency.py tests/test_epic5_order_fsm.py -v -s --tb=short
```

---

## 📝 Resumen de Comandos Más Usados

```bash
# 1. Validación completa (RECOMENDADO)
cd /mnt/c/VS\ projects/alpaca && python scripts/validate_epic4_5_simple.py

# 2. Todos los tests
cd /mnt/c/VS\ projects/alpaca && pytest tests/test_epic4_idempotency.py tests/test_epic5_order_fsm.py -v

# 3. Validación rápida Epic 4
cd /mnt/c/VS\ projects/alpaca && python -c "import sys; sys.path.insert(0, '.'); from lib.models import OrderIntent, SignalSide; from decimal import Decimal; o = OrderIntent(symbol='GOOGL', side=SignalSide.BUY, quantity=Decimal('100'), client_order_id='test', signal_source='test', price=Decimal('150')); id1 = o.generate_client_order_id(); id2 = o.generate_client_order_id(); print(f'Epic 4: {\"✅ OK\" if (id1 == id2 and id1.startswith(\"risk_\")) else \"❌ FAIL\"}')"

# 4. Validación rápida Epic 5
cd /mnt/c/VS\ projects/alpaca && python -c "import sys; sys.path.insert(0, '.'); from lib.order_fsm import OrderFSM, OrderState, OrderEvent; from lib.models import OrderIntent, SignalSide; from decimal import Decimal; o = OrderIntent(symbol='TSLA', side=SignalSide.BUY, quantity=Decimal('50'), client_order_id='test', signal_source='test', price=Decimal('700')); fsm = OrderFSM(o, 'broker_123'); s1 = fsm.current_state == OrderState.NEW; fsm.transition(OrderEvent.SUBMIT); s2 = fsm.current_state == OrderState.SUBMITTED; print(f'Epic 5: {\"✅ OK\" if (s1 and s2) else \"❌ FAIL\"}')"
```

---

## ✅ Comando TODO-EN-UNO

Si quieres ejecutar TODAS las validaciones de una vez:

```bash
cd /mnt/c/VS\ projects/alpaca && echo "=== Validación Simple ===" && python scripts/validate_epic4_5_simple.py && echo -e "\n=== Tests Unitarios ===" && pytest tests/test_epic4_idempotency.py tests/test_epic5_order_fsm.py -v --tb=short
```

---

**¡Listo para copiar y pegar!** Usa principalmente la **Opción 1** (script simple) para una validación rápida y completa.
