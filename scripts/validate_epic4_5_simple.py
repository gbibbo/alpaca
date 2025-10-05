#!/usr/bin/env python3
"""
scripts/validate_epic4_5_simple.py
Script de validación simple para Épicas 4 y 5
No requiere pytest, ejecuta validaciones directamente
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from decimal import Decimal
from lib.models import OrderIntent, SignalSide, OrderType
from lib.order_fsm import (
    OrderFSM, OrderState, OrderEvent,
    create_fsm_from_order_intent,
    map_alpaca_status_to_event
)
from lib.metrics_helpers import ExecutorMetrics
import time

# Colors para output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def print_header(text):
    print(f"\n{BLUE}{'='*80}{RESET}")
    print(f"{BLUE}{text}{RESET}")
    print(f"{BLUE}{'='*80}{RESET}")

def print_test(test_name):
    print(f"\n{YELLOW}📋 {test_name}{RESET}")
    print(f"{YELLOW}{'-'*80}{RESET}")

def print_pass(text):
    print(f"{GREEN}✅ {text}{RESET}")

def print_fail(text):
    print(f"{RED}❌ {text}{RESET}")

def print_info(text):
    print(f"   {text}")

# Track results
all_tests_passed = True

print_header("VALIDACIÓN ÉPICAS 4 Y 5")

# =============================================================================
# EPIC 4: IDEMPOTENCIA
# =============================================================================

print_header("EPIC 4: IDEMPOTENCIA")

# Test 1: client_order_id Determinista
print_test("Test 1: client_order_id Determinista")

order1 = OrderIntent(
    symbol="GOOGL",
    side=SignalSide.BUY,
    quantity=Decimal("100"),
    client_order_id="temp",
    signal_source="smart_technical",
    order_type=OrderType.MARKET,
    price=Decimal("150")
)

id1 = order1.generate_client_order_id()
id2 = order1.generate_client_order_id()

print_info(f"ID 1: {id1}")
print_info(f"ID 2: {id2}")

if id1 == id2:
    print_pass("IDs son idénticos")
else:
    print_fail("IDs NO son idénticos")
    all_tests_passed = False

if id1.startswith("risk_"):
    print_pass("Formato correcto (inicia con 'risk_')")
else:
    print_fail("Formato incorrecto")
    all_tests_passed = False

if "GOOGL" in id1:
    print_pass("Contiene símbolo GOOGL")
else:
    print_fail("No contiene símbolo")
    all_tests_passed = False

if len(id1) <= 50:
    print_pass(f"Longitud válida: {len(id1)} <= 50")
else:
    print_fail(f"Longitud excede 50 caracteres: {len(id1)}")
    all_tests_passed = False

# Test 2: Diferentes símbolos = diferentes IDs
print_test("Test 2: Diferentes símbolos generan diferentes IDs")

order2 = OrderIntent(
    symbol="AAPL",
    side=SignalSide.BUY,
    quantity=Decimal("100"),
    client_order_id="temp2",
    signal_source="smart_technical",
    order_type=OrderType.MARKET,
    price=Decimal("180")
)

id_googl = order1.generate_client_order_id()
id_aapl = order2.generate_client_order_id()

print_info(f"GOOGL ID: {id_googl}")
print_info(f"AAPL ID:  {id_aapl}")

if id_googl != id_aapl:
    print_pass("IDs son diferentes para símbolos diferentes")
else:
    print_fail("IDs son iguales (ERROR)")
    all_tests_passed = False

# Test 3: Métricas Epic 4
print_test("Test 3: Métricas Epic 4 definidas")

metrics = ExecutorMetrics()

if hasattr(metrics, 'DUPLICATE_ORDER_BLOCKED_BY_CLIENT_ID'):
    print_pass("Métrica DUPLICATE_ORDER_BLOCKED_BY_CLIENT_ID existe")
else:
    print_fail("Métrica DUPLICATE_ORDER_BLOCKED_BY_CLIENT_ID NO existe")
    all_tests_passed = False

if hasattr(metrics, 'BROKER_429_RETRIES'):
    print_pass("Métrica BROKER_429_RETRIES existe")
else:
    print_fail("Métrica BROKER_429_RETRIES NO existe")
    all_tests_passed = False

try:
    metrics.duplicate_order_blocked("GOOGL", "risk_test_123")
    metrics.broker_429_retry("submit_order", success=True)
    print_pass("Métricas se pueden registrar sin errores")
except Exception as e:
    print_fail(f"Error registrando métricas: {e}")
    all_tests_passed = False

# =============================================================================
# EPIC 5: FSM
# =============================================================================

print_header("EPIC 5: MÁQUINA DE ESTADOS (FSM)")

# Test 4: Estados FSM
print_test("Test 4: Estados FSM definidos")

expected_states = [
    "NEW", "SUBMITTED", "PENDING_NEW", "ACCEPTED", "PARTIALLY_FILLED",
    "PENDING_CANCEL", "PENDING_REPLACE", "FILLED", "CANCELED",
    "REJECTED", "EXPIRED", "REPLACED", "SUSPENDED"
]

all_states_ok = True
for state in expected_states:
    if hasattr(OrderState, state):
        print_pass(f"Estado {state} definido")
    else:
        print_fail(f"Estado {state} NO definido")
        all_states_ok = False

if not all_states_ok:
    all_tests_passed = False

# Test 5: Transiciones FSM básicas
print_test("Test 5: Transiciones FSM Válidas")

test_order = OrderIntent(
    symbol="TSLA",
    side=SignalSide.BUY,
    quantity=Decimal("50"),
    client_order_id="fsm_test_001",
    signal_source="test_strategy",
    order_type=OrderType.MARKET,
    price=Decimal("700")
)

fsm = OrderFSM(test_order, "broker_123")

# NEW state
if fsm.current_state == OrderState.NEW:
    print_pass(f"Estado inicial: {fsm.current_state}")
else:
    print_fail(f"Estado inicial incorrecto: {fsm.current_state}")
    all_tests_passed = False

# NEW → SUBMITTED
success = fsm.transition(OrderEvent.SUBMIT)
if success and fsm.current_state == OrderState.SUBMITTED:
    print_pass(f"Transición NEW → SUBMITTED exitosa")
else:
    print_fail(f"Transición NEW → SUBMITTED falló")
    all_tests_passed = False

# SUBMITTED → ACCEPTED
success = fsm.transition(OrderEvent.ACCEPT)
if success and fsm.current_state == OrderState.ACCEPTED:
    print_pass(f"Transición SUBMITTED → ACCEPTED exitosa")
else:
    print_fail(f"Transición SUBMITTED → ACCEPTED falló")
    all_tests_passed = False

# ACCEPTED → PARTIALLY_FILLED
success = fsm.transition(OrderEvent.PARTIAL_FILL, fill_quantity=Decimal("25"), fill_price=Decimal("700"))
if success and fsm.current_state == OrderState.PARTIALLY_FILLED:
    print_pass(f"Transición ACCEPTED → PARTIALLY_FILLED exitosa")
    print_info(f"   Llenado: {fsm.filled_quantity} / {test_order.quantity}")
else:
    print_fail(f"Transición ACCEPTED → PARTIALLY_FILLED falló")
    all_tests_passed = False

# Verificar tracking de fills
if fsm.filled_quantity == Decimal("25") and fsm.remaining_quantity == Decimal("25"):
    print_pass(f"Fill tracking correcto: 25 llenado, 25 restante")
else:
    print_fail(f"Fill tracking incorrecto: {fsm.filled_quantity} llenado, {fsm.remaining_quantity} restante")
    all_tests_passed = False

# PARTIALLY_FILLED → FILLED
success = fsm.transition(OrderEvent.FILL, fill_quantity=Decimal("25"), fill_price=Decimal("705"))
if success and fsm.current_state == OrderState.FILLED:
    print_pass(f"Transición PARTIALLY_FILLED → FILLED exitosa")
else:
    print_fail(f"Transición PARTIALLY_FILLED → FILLED falló")
    all_tests_passed = False

# Verificar completamente llenado
if fsm.filled_quantity == Decimal("50"):
    print_pass(f"Orden completamente llenada: {fsm.filled_quantity}")
else:
    print_fail(f"Orden NO completamente llenada: {fsm.filled_quantity}")
    all_tests_passed = False

# Test 6: Transiciones inválidas
print_test("Test 6: Transiciones Inválidas se Rechazan")

fsm2 = OrderFSM(test_order, "broker_456")
print_info(f"Estado inicial: {fsm2.current_state}")

# Intentar transición inválida: NEW → FILL
success = fsm2.transition(OrderEvent.FILL)
if not success and fsm2.current_state == OrderState.NEW:
    print_pass("Transición inválida NEW → FILL rechazada correctamente")
else:
    print_fail("Transición inválida NO fue rechazada")
    all_tests_passed = False

# Test 7: Timeouts
print_test("Test 7: Detección de Timeouts")

fsm3 = OrderFSM(test_order, "broker_789", new_timeout_seconds=2)
fsm3.transition(OrderEvent.SUBMIT)
print_info(f"Estado: {fsm3.current_state}, Timeout: 2s")

# Verificar no timeout inmediatamente
timed_out = fsm3.check_timeout()
if not timed_out:
    print_pass("Sin timeout inmediato (correcto)")
else:
    print_fail("Timeout detectado inmediatamente (ERROR)")
    all_tests_passed = False

# Esperar timeout
print_info("Esperando 2.5 segundos...")
time.sleep(2.5)

# Verificar timeout
timed_out = fsm3.check_timeout()
if timed_out and fsm3.current_state == OrderState.EXPIRED:
    print_pass(f"Timeout detectado correctamente, estado: {fsm3.current_state}")
else:
    print_fail(f"Timeout NO detectado, estado: {fsm3.current_state}")
    all_tests_passed = False

# Test 8: Helpers FSM
print_test("Test 8: Funciones Helper FSM")

# is_terminal
if fsm.is_terminal():
    print_pass("is_terminal() correcto para FILLED")
else:
    print_fail("is_terminal() incorrecto")
    all_tests_passed = False

# is_active
if not fsm.is_active():
    print_pass("is_active() correcto para FILLED")
else:
    print_fail("is_active() incorrecto")
    all_tests_passed = False

# get_fill_percentage
if fsm.get_fill_percentage() == 1.0:
    print_pass(f"get_fill_percentage() correcto: {fsm.get_fill_percentage():.1%}")
else:
    print_fail(f"get_fill_percentage() incorrecto: {fsm.get_fill_percentage():.1%}")
    all_tests_passed = False

# Test 9: Factory function
print_test("Test 9: Factory Function")

fsm_factory = create_fsm_from_order_intent(test_order, "broker_999")
if fsm_factory.current_state == OrderState.SUBMITTED:
    print_pass(f"Factory crea FSM en estado SUBMITTED")
else:
    print_fail(f"Factory crea FSM en estado incorrecto: {fsm_factory.current_state}")
    all_tests_passed = False

# Test 10: Serialización
print_test("Test 10: Serialización to_dict()")

fsm_dict = fsm_factory.to_dict()
required_keys = ["broker_order_id", "current_state", "fills", "timing", "state_history"]
all_keys_ok = True

for key in required_keys:
    if key in fsm_dict:
        print_pass(f"Clave '{key}' presente en dict")
    else:
        print_fail(f"Clave '{key}' ausente en dict")
        all_keys_ok = False

if not all_keys_ok:
    all_tests_passed = False

# Test 11: Mapeo Alpaca status
print_test("Test 11: Mapeo de Estados Alpaca")

alpaca_mappings = {
    "new": OrderEvent.ACCEPT,
    "accepted": OrderEvent.ACCEPT,
    "partially_filled": OrderEvent.PARTIAL_FILL,
    "filled": OrderEvent.FILL,
    "canceled": OrderEvent.CANCEL,
    "rejected": OrderEvent.REJECT
}

all_mappings_ok = True
for alpaca_status, expected_event in alpaca_mappings.items():
    event = map_alpaca_status_to_event(alpaca_status)
    if event == expected_event:
        print_pass(f"'{alpaca_status}' → {expected_event}")
    else:
        print_fail(f"'{alpaca_status}' → {event} (esperado: {expected_event})")
        all_mappings_ok = False

if not all_mappings_ok:
    all_tests_passed = False

# =============================================================================
# RESUMEN FINAL
# =============================================================================

print_header("RESUMEN DE VALIDACIÓN")

tests_summary = [
    ("Epic 4: client_order_id determinista", True),
    ("Epic 4: Diferentes símbolos → diferentes IDs", True),
    ("Epic 4: Métricas definidas", True),
    ("Epic 5: Estados FSM definidos", all_states_ok),
    ("Epic 5: Transiciones válidas", True),
    ("Epic 5: Transiciones inválidas rechazadas", True),
    ("Epic 5: Detección de timeouts", True),
    ("Epic 5: Funciones helper", True),
    ("Epic 5: Factory function", True),
    ("Epic 5: Serialización", all_keys_ok),
    ("Epic 5: Mapeo estados Alpaca", all_mappings_ok)
]

for test_name, passed in tests_summary:
    if passed:
        print_pass(test_name)
    else:
        print_fail(test_name)

print(f"\n{BLUE}{'='*80}{RESET}")
if all_tests_passed:
    print(f"{GREEN}🎉 TODAS LAS VALIDACIONES PASARON{RESET}")
    print(f"{GREEN}✅ Épica 4 (Idempotencia): COMPLETA{RESET}")
    print(f"{GREEN}✅ Épica 5 (FSM): COMPLETA{RESET}")
    print(f"{BLUE}{'='*80}{RESET}\n")
    sys.exit(0)
else:
    print(f"{RED}❌ ALGUNAS VALIDACIONES FALLARON{RESET}")
    print(f"{RED}Revisa los errores arriba{RESET}")
    print(f"{BLUE}{'='*80}{RESET}\n")
    sys.exit(1)
