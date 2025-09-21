import logging
import time
import threading
import uuid
from pydantic import BaseModel
from enum import Enum
from collections import defaultdict
import fakeredis # Necesitas instalarlo con: pip install fakeredis pydantic

# --- Configuración del Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

# --- 1. Simulación de tus Modelos de Datos (lib/models.py) ---

class SignalSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"

class OrderStatus(str, Enum):
    ACCEPTED = "accepted"
    FILLED = "filled"

class Signal(BaseModel):
    source: str
    symbol: str
    side: SignalSide
    price: float
    confidence: float

class OrderIntent(BaseModel):
    symbol: str
    side: SignalSide
    price: float
    quantity: int

class OrderFill(BaseModel):
    symbol: str
    side: SignalSide
    price: float
    quantity: int
    broker_order_id: uuid.UUID

# --- 2. Simulación de tu MessageBus (lib/bus.py) ---

class MessageBus:
    """
    MessageBus con un despachador (fan-out) para permitir múltiples
    suscriptores por canal/patrón en una única instancia de PubSub.
    """
    def __init__(self, use_fake_redis=True):
        self.client = fakeredis.FakeStrictRedis(decode_responses=True)
        self.pubsub = self.client.pubsub(ignore_subscribe_messages=True)
        # <<-- CAMBIO ARQUITECTÓNICO: Diccionarios para gestionar listas de handlers.
        self._chan_handlers = defaultdict(list)
        self._pat_handlers = defaultdict(list)
        log.info("Using fakeredis with fan-out dispatcher")

    def _dispatch_channel(self, message: dict):
        channel = message['channel']
        for handler in self._chan_handlers.get(channel, []):
            handler(message)

    def _dispatch_pattern(self, message: dict):
        pattern = message['pattern']
        for handler in self._pat_handlers.get(pattern, []):
            handler(message)

    def publish(self, channel: str, message: BaseModel):
        self.client.publish(channel, message.model_dump_json())

    def subscribe(self, channel: str, handler):
        is_first_handler = not self._chan_handlers[channel]
        self._chan_handlers[channel].append(handler)
        if is_first_handler:
            self.pubsub.subscribe(**{channel: self._dispatch_channel})

    def psubscribe(self, pattern: str, handler):
        is_first_handler = not self._pat_handlers[pattern]
        self._pat_handlers[pattern].append(handler)
        if is_first_handler:
            self.pubsub.psubscribe(**{pattern: self._dispatch_pattern})

    def run_in_thread(self):
        thread = self.pubsub.run_in_thread(sleep_time=0.01)
        log.info("Message bus listener started in background thread.")
        return thread

# --- 3. Simulación de tus Microservicios (apps/) ---

class RiskManager:
    def __init__(self, bus: MessageBus, config: dict):
        self.bus = bus
        self.config = config
        self.portfolio_value = config.get("portfolio_value", 100000)
        self.max_pos_size = config.get("max_position_size", 0.10)

    def process_signal(self, message):
        signal = Signal.model_validate_json(message['data'])
        log.info(f"Processing signal: {signal.side.value} {signal.symbol} from {signal.source}")
        
        position_size_usd = self.portfolio_value * self.max_pos_size
        # <<-- MEJORA: Asegurar que la cantidad sea al menos 1.
        quantity = max(1, int(position_size_usd // signal.price))

        intent = OrderIntent(symbol=signal.symbol, side=signal.side, price=signal.price, quantity=quantity)
        log.info(f"Created order intent: {intent.side.value} {intent.quantity} {intent.symbol}")
        self.bus.publish("orders.intent", intent)

    def start_consuming(self):
        self.bus.psubscribe("signals.*", self.process_signal)

class Executor:
    def __init__(self, bus: MessageBus, config: dict):
        self.bus = bus
        self.config = config

    def execute_order(self, message):
        intent = OrderIntent.model_validate_json(message['data'])
        log.info(f"Received order intent: {intent.side.value} {intent.quantity} {intent.symbol}")

        try:
            mock_alpaca_order = type('MockOrder', (object,), {'id': uuid.uuid4()})
            log.info(f"Order submitted successfully: Alpaca Order ID: {mock_alpaca_order.id}")

            fill_event = OrderFill(
                symbol=intent.symbol, side=intent.side, price=intent.price,
                quantity=intent.quantity, broker_order_id=mock_alpaca_order.id
            )
            
            fill_channel = f"orders.fill.{intent.symbol}"
            self.bus.publish(fill_channel, fill_event)
            log.info(f"Published fill event to {fill_channel}")
        except Exception as e:
            log.error(f"Failed to execute order for {intent.symbol}: {e}")

    def start_consuming(self):
        self.bus.subscribe("orders.intent", self.execute_order)

# --- 4. El Script de Test (test_forced_signals.py) ---

if __name__ == "__main__":
    log.info("🧪 FORCED SIGNAL PIPELINE TEST")
    log.info("=" * 60)

    test_config = {"max_position_size": 0.10, "portfolio_value": 100000.00}
    order_intents_created = []
    orders_executed = []

    def handle_intent(message):
        intent = OrderIntent.model_validate_json(message['data'])
        order_intents_created.append(intent)
        log.info(f"[TEST] 💼 INTENT CAPTURED: {intent.symbol}")

    def handle_fill(message):
        fill = OrderFill.model_validate_json(message['data'])
        orders_executed.append(fill)
        log.info(f"[TEST] ✅ FILL CAPTURED: {fill.symbol}")

    bus = MessageBus()
    risk_manager = RiskManager(bus, test_config)
    executor = Executor(bus, test_config)

    # Suscribir los handlers del test y de los servicios al mismo bus
    bus.subscribe("orders.intent", handle_intent)
    bus.psubscribe("orders.fill.*", handle_fill)
    risk_manager.start_consuming()
    executor.start_consuming()

    bus_thread = bus.run_in_thread()
    time.sleep(0.5) # Breve pausa para asegurar que los suscriptores estén listos

    signals_to_send = [
        Signal(source="manual_test", symbol="AAPL", side=SignalSide.BUY, price=238.50, confidence=0.80),
        Signal(source="manual_test", symbol="MSFT", side=SignalSide.BUY, price=412.30, confidence=0.75),
        Signal(source="manual_test", symbol="GOOGL", side=SignalSide.BUY, price=161.25, confidence=0.70),
    ]

    for signal in signals_to_send:
        bus.publish(f"signals.{signal.symbol}", signal)
    
    # <<-- MEJORA: Espera robusta en lugar de sleep fijo.
    log.info("⏳ Waiting for all fill events...")
    timeout = 10  # segundos
    start_time = time.time()
    while len(orders_executed) < len(signals_to_send) and time.time() - start_time < timeout:
        time.sleep(0.1)

    bus_thread.stop()
    bus_thread.join(timeout=1)
    log.info("Bus thread stopped cleanly.")

    log.info("=" * 60)
    log.info("FORCED SIGNAL TEST RESULTS")
    log.info("=" * 60)
    log.info(f"📊 Manual Signals Created: {len(signals_to_send)}")
    log.info(f"💼 Order Intents Created: {len(order_intents_created)}")
    log.info(f"✅ Orders Executed: {len(orders_executed)}")
    log.info("-" * 60)

    if len(order_intents_created) == len(signals_to_send) and len(orders_executed) == len(signals_to_send):
        log.info("✅ Pipeline execution successful and fully verified!")
    else:
        log.info("❌ Pipeline execution failed or was not fully verified.")

    log.info("=" * 60)