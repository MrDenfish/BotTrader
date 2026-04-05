# Plugin Interfaces

**Source**: `v2/core/interfaces.py`, `v2/core/types.py`, `v2/core/event_bus.py`, `v2/core/registry.py`

All 8 plugin ABCs, the shared type system, event bus, and registry.

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

**Implementations**: `coinbase` (live Coinbase Advanced Trade), `kraken` (live Kraken REST + WS v2 executions), `paper` (simulated with live ticker prices), `backtest` (no-op), `backtest_sim` (event-driven fill simulation)

### DataProvider

Supplies market data. Publishes `CandleEvent` and/or `TickerEvent` via the event bus.

```python
class DataProvider(ABC):
    name: str

    async def start(self, symbols: list[str]) -> None: ...
    async def stop(self) -> None: ...
```

**Implementations**: `websocket` (Coinbase WS ticker → 1m candle aggregation), `kraken_websocket` (Kraken WS v2 ticker → 1m candle aggregation, REST volume backfill), `csv_replay` (historical CSV replay for backtesting)

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

**Implementations**: `composite_scoring` (production: 8 indicator families, weighted scoring, guardrails), `hybrid_4h_maker` (backtest: Donchian breakout + compression), `random_entry` (diagnostic: Poisson-distributed baseline for MFE/MAE analysis)

### RiskManager

Validates signals against risk limits before execution. Multiple risk managers run in sequence (chain pattern) — each can veto.

```python
class RiskManager(ABC):
    name: str

    def configure(self, config: Any) -> None: ...
    def check_signal(self, signal: Signal, portfolio: Portfolio) -> Signal | None: ...
    def on_fill(self, fill: Fill, portfolio: Portfolio) -> None: ...         # optional
    def on_position_update(self, position: Position) -> Signal | None: ...  # optional
```

`check_signal` returns the signal (approved) or `None` (vetoed). May publish `RiskEvent` via event bus when vetoing.

**Implementations**: `basic` (exposure limits, daily loss, HODL gate, fee hurdle, FIFO protection), `exit_manager` (dynamic exits: hard/soft/trailing stops, time limit, peak tracking — all fee-aware), `performance_filter` (symbol exclusion based on rolling win rate / P&L), `circuit_breaker` (drawdown protection: max losses in window, large loss threshold, cooldown)

### ExecutionManager

Translates approved signals into exchange orders.

```python
class ExecutionManager(ABC):
    name: str

    def configure(self, config: Any) -> None: ...
    async def execute_signal(self, signal: Signal, exchange: ExchangeAdapter) -> Order | None: ...
```

Handles price adjustment, sizing, TP/SL, retries.

**Implementations**: `maker_only` (post-only limit with buffer escalation, trigger-based sizing, stale order cancellation, buy TTL), `bracket` (TP/SL brackets)

### StorageAdapter

Persists trades, positions, and strategy state.

```python
class StorageAdapter(ABC):
    name: str

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    def set_exchange_name(self, name: str) -> None: ...       # tag records by exchange
    async def record_fill(self, fill: Fill) -> None: ...
    async def record_order(self, order: Order) -> None: ...
    async def get_positions(self, symbol: str | None = None) -> list[Position]: ...
    async def save_position(self, position: Position) -> None: ...  # upsert on fill
    async def get_trades(self, symbol: str | None = None, since: datetime | None = None) -> list[Fill]: ...
    async def save_state(self, key: str, state: dict) -> None: ...
    async def load_state(self, key: str) -> dict | None: ...
```

**Implementations**: `postgres` (production — tables: `v2_fills`, `v2_orders`, `v2_positions`, `v2_state`), `sqlite` (backtest/local)

### Observer

Observability — logging, metrics, alerts. Subscribes to all events via `subscribe_all`.

```python
class Observer(ABC):
    name: str

    def on_event(self, event: Any) -> None: ...
```

**Implementations**: `structured_log` (JSON event logging), `signal_comparison` (JSONL signal log for analysis), `heartbeat` (periodic health check file), `alerting` (alert generation on risk events), `daily_report` (legacy simple report), `daily_report_v2` (production: modular report with 7 collectors, HTML + Slack renderers, SMTP + Slack delivery), `backtest_diagnostics` (MFE/MAE, post-exit tracking, regime snapshots), `backtest_results` (backtest summary statistics)

### PairDiscovery

Discovers tradeable pairs from an exchange based on volume and liquidity.

```python
class PairDiscovery(ABC):
    name: str

    def configure(self, config: Any) -> None: ...
    async def discover(self) -> list[str]: ...    # fetch and filter trading pairs
    async def start(self) -> None: ...            # start periodic refresh
    async def stop(self) -> None: ...             # stop refresh and close resources
```

Publishes `SymbolsUpdatedEvent` when the active pair set changes. Data providers subscribe to dynamically add/remove symbol subscriptions.

**Implementations**: `kraken` (production: Kraken public API, volume + bid-ask spread filtering, seed symbol guarantee), `coinbase` (Coinbase REST pair discovery), `csv` (static CSV-based pair list)

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
| `TickerEvent` | `symbol, price, change_24h_pct, bid, ask` | DataProvider |
| `SignalEvent` | `signal: Signal`, `strategy_name: str` | App (after strategy.on_candle) |
| `OrderEvent` | `order: Order`, `event_type: str` | ExecutionManager |
| `FillEvent` | `fill: Fill` | ExchangeAdapter |
| `PositionEvent` | `position: Position`, `event_type: str` | App (after fill processing) |
| `RiskEvent` | `event_type: str`, `reason: str`, `metadata: dict` | RiskManager |
| `SymbolsUpdatedEvent` | `symbols, added, removed, source` | PairDiscovery |

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

**Important**: Never put `@plugin` in `__init__.py` — `discover_plugins()` skips `__init__` modules.

### Auto-Discovery

```python
from v2.core.registry import discover_plugins

discover_plugins()  # imports all v2.plugins.* modules, triggering @plugin decorators
```

Skips modules starting with `_` and modules named `base` (ABCs, not concrete plugins).

### Factory Functions

```python
from v2.core.registry import create_exchange, create_strategy, create_risk

exchange = create_exchange("kraken", event_bus=bus, key_file="...")
strategy = create_strategy("composite_scoring", event_bus=bus)
risk = create_risk("basic", event_bus=bus, pass_through=False)
```

### Introspection

```python
from v2.core.registry import list_plugins

list_plugins()
# {'exchange': ['backtest', 'paper', 'kraken', 'coinbase', 'backtest_sim'],
#  'data': ['websocket', 'kraken_websocket', 'csv_replay'],
#  'strategy': ['composite_scoring', 'hybrid_4h_maker', 'random_entry'],
#  'risk': ['basic', 'exit_manager', 'performance_filter', 'circuit_breaker'],
#  'execution': ['maker_only', 'bracket'],
#  'storage': ['postgres', 'sqlite'],
#  'observer': ['structured_log', 'signal_comparison', 'heartbeat', 'alerting',
#               'daily_report', 'daily_report_v2', 'backtest_diagnostics', 'backtest_results'],
#  'pair_discovery': ['kraken', 'coinbase', 'csv']}
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
    config:
      threshold: 3.0
```

## Last Updated

2026-04-03
