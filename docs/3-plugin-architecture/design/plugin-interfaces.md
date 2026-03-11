# Plugin Interfaces

**Source**: `v2/core/interfaces.py`, `v2/core/types.py`, `v2/core/event_bus.py`, `v2/core/registry.py`

All 7 plugin ABCs, the shared type system, event bus, and registry.

---

## Plugin ABCs

Each plugin implements exactly one ABC. All ABCs live in `v2/core/interfaces.py`.

### ExchangeAdapter

Connects to an exchange for order management and account data.

```python
class ExchangeAdapter(ABC):
    name: str

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...

    # Orders
    async def submit_order(self, order: Order) -> Order: ...
    async def cancel_order(self, order_id: str) -> bool: ...
    async def get_order(self, order_id: str) -> Order: ...
    async def get_open_orders(self, symbol: str | None = None) -> list[Order]: ...

    # Account
    async def get_balances(self) -> dict[str, Decimal]: ...
    async def get_fee_rates(self) -> dict[str, Decimal]: ...

    # Market data (basic — full data via DataProvider)
    async def get_ticker(self, symbol: str) -> TickerEvent: ...
```

**Implementations**: `coinbase` (live), `paper` (simulated), `backtest` (no-op)

### DataProvider

Supplies market data. Publishes `CandleEvent` and/or `TickerEvent` via the event bus.

```python
class DataProvider(ABC):
    name: str

    async def start(self, symbols: list[str]) -> None: ...
    async def stop(self) -> None: ...
```

**Implementations**: `websocket` (live ticker → 1m candle aggregation), `csv_replay` (historical CSV with indicator pre-computation)

### Strategy

Trading decision logic. Receives candles, emits signals.

```python
class Strategy(ABC):
    name: str
    version: str

    def configure(self, config: Any) -> None: ...
    def warmup_bars(self) -> dict[str, int]: ...      # min bars per timeframe before trading
    def start(self) -> None: ...                       # optional lifecycle hook
    def stop(self) -> None: ...                        # optional lifecycle hook
    def on_candle(self, candle: Candle, indicators: dict) -> Signal | None: ...
    def on_fill(self, fill: Fill) -> Signal | None: ...       # optional
    def on_ticker(self, ticker: TickerEvent) -> Signal | None: ...  # optional
    def get_state(self) -> dict: ...                   # serialize for checkpointing
    def load_state(self, state: dict) -> None: ...     # restore from checkpoint
```

**Implementations**: `composite_scoring` (production multi-indicator), `hybrid_4h_maker` (ROC momentum backtest)

### RiskManager

Validates signals against risk limits before execution.

```python
class RiskManager(ABC):
    name: str

    def configure(self, config: Any) -> None: ...
    def check_signal(self, signal: Signal, portfolio: Portfolio) -> Signal | None: ...
    def on_fill(self, fill: Fill, portfolio: Portfolio) -> None: ...         # optional
    def on_position_update(self, position: Position) -> Signal | None: ...  # optional
```

`check_signal` returns the signal (approved) or `None` (vetoed). May publish `RiskEvent` via event bus when vetoing.

**Implementations**: `basic` (exposure limits, daily loss, HODL gate, position count), `circuit_breaker` (drawdown monitoring)

### ExecutionManager

Translates approved signals into exchange orders.

```python
class ExecutionManager(ABC):
    name: str

    def configure(self, config: Any) -> None: ...
    async def execute_signal(self, signal: Signal, exchange: ExchangeAdapter) -> Order | None: ...
```

Handles price adjustment, sizing, TP/SL, retries.

**Implementations**: `maker_only` (post-only limit with buffer escalation), `bracket` (TP/SL brackets)

### StorageAdapter

Persists trades, positions, and strategy state.

```python
class StorageAdapter(ABC):
    name: str

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def record_fill(self, fill: Fill) -> None: ...
    async def record_order(self, order: Order) -> None: ...
    async def get_positions(self, symbol: str | None = None) -> list[Position]: ...
    async def get_trades(self, symbol: str | None = None, since: datetime | None = None) -> list[Fill]: ...
    async def save_state(self, key: str, state: dict) -> None: ...
    async def load_state(self, key: str) -> dict | None: ...
```

**Implementations**: `postgres` (production, v2_* prefixed tables), `sqlite` (backtest/local)

### Observer

Observability — logging, metrics, alerts. Subscribes to all events via `subscribe_all`.

```python
class Observer(ABC):
    name: str

    def on_event(self, event: Any) -> None: ...
```

**Implementations**: `structured_log`, `signal_comparison` (JSONL for v1/v2 comparison), `heartbeat`, `alerting`, `daily_report`

---

## Shared Types

All types live in `v2/core/types.py`. Plugins communicate exclusively through these — no plugin-specific types cross boundaries.

### Enums

| Enum | Values |
|------|--------|
| `Direction` | `BUY`, `SELL`, `HOLD` |
| `Side` | `BUY`, `SELL` |
| `OrderType` | `LIMIT`, `MARKET`, `STOP` |
| `OrderStatus` | `PENDING`, `OPEN`, `FILLED`, `PARTIALLY_FILLED`, `CANCELLED`, `REJECTED` |

### Data Classes

| Type | Key Fields | Notes |
|------|-----------|-------|
| `Candle` | symbol, timestamp, OHLCV, timeframe | Frozen. Default timeframe `"1m"` |
| `Signal` | direction, symbol, timestamp, price, qty, notional, reason, metadata | Frozen. Strategy output |
| `Order` | order_id, symbol, side, order_type, price, qty, status, timestamp | Frozen. Decimal price/qty |
| `Fill` | fill_id, order_id, symbol, side, price, qty, fee, is_maker, timestamp | Frozen. Decimal values |
| `Position` | symbol, qty, avg_entry_price, cost_basis, unrealized/realized_pnl | Mutable |
| `Portfolio` | cash_balance, positions (dict), total_equity, timestamp | Mutable |

### Events

| Event | Payload | Published By |
|-------|---------|-------------|
| `CandleEvent` | `candle: Candle` | DataProvider |
| `SignalEvent` | `signal: Signal`, `strategy_name: str` | App (after strategy.on_candle) |
| `OrderEvent` | `order: Order`, `event_type: str` | ExecutionManager |
| `FillEvent` | `fill: Fill` | ExchangeAdapter |
| `PositionEvent` | `position: Position`, `event_type: str` | App (after fill processing) |
| `RiskEvent` | `event_type: str`, `reason: str`, `metadata: dict` | RiskManager |
| `TickerEvent` | `symbol: str`, `price: float`, `change_24h_pct`, `bid`, `ask` | DataProvider |

---

## EventBus

`v2/core/event_bus.py` — typed publish/subscribe. Plugins never reference each other directly.

```python
bus = EventBus()

# Subscribe to a specific event type
bus.subscribe(CandleEvent, my_handler)

# Subscribe to all events (used by Observers)
bus.subscribe_all(observer.on_event)

# Publish (synchronous — handlers called in subscription order)
bus.publish(CandleEvent(candle=...))

# Publish (async — awaits async handlers)
await bus.publish_async(event)
```

Error isolation: if a handler raises, the exception is logged and remaining handlers still execute.

---

## Registry

`v2/core/registry.py` — plugin discovery and instantiation.

### Registration via Decorator

```python
from v2.core.registry import plugin

@plugin("exchange", "coinbase")
class CoinbaseExchange(ExchangeAdapter):
    ...
```

### Auto-Discovery

```python
from v2.core.registry import discover_plugins

discover_plugins()  # imports all v2.plugins.* modules, triggering @plugin decorators
```

Skips modules starting with `_` and modules named `base` (ABCs, not concrete plugins).

### Factory Functions

```python
from v2.core.registry import create_exchange, create_strategy, create_risk

exchange = create_exchange("coinbase", bus=bus)
strategy = create_strategy("composite_scoring", bus=bus)
risk = create_risk("basic", bus=bus)
```

### Introspection

```python
from v2.core.registry import list_plugins

list_plugins()
# {'exchange': ['coinbase', 'paper', 'backtest'],
#  'data': ['websocket', 'csv_replay'],
#  'strategy': ['composite_scoring', 'hybrid_4h_maker'],
#  'risk': ['basic', 'circuit_breaker'],
#  'execution': ['maker_only', 'bracket'],
#  'storage': ['postgres', 'sqlite'],
#  'observer': ['structured_log', 'signal_comparison', 'heartbeat', 'alerting', 'daily_report']}
```

---

## Writing a New Plugin

1. Create a module under the appropriate `v2/plugins/<category>/` directory
2. Subclass the corresponding ABC from `v2.core.interfaces`
3. Decorate with `@plugin("category", "name")`
4. Auto-discovery will find it on next startup

```python
from v2.core.interfaces import Strategy
from v2.core.registry import plugin
from v2.core.types import Candle, Signal

@plugin("strategy", "my_strategy")
class MyStrategy(Strategy):
    name = "my_strategy"
    version = "1.0"

    def configure(self, config):
        self._threshold = config.get("threshold", 2.0)

    def warmup_bars(self):
        return {"1m": 100}

    def on_candle(self, candle, indicators):
        # Your logic here
        return None  # or Signal(...)
```

Then reference it in your config YAML:

```yaml
strategies:
  - type: "my_strategy"
    threshold: 3.0
```
