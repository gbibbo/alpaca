# ALPACA Algorithmic Trading Platform

Python-based algorithmic trading platform with market data ingestion, risk controls, backtesting, and real-time monitoring.

This repository documents a service-oriented financial software prototype built around market data, event processing, simulation, risk controls, persistence, API access, and monitoring.

> This project is a software engineering prototype. It is not financial advice and should not be treated as a recommendation to trade.

## Overview

ALPACA is a Python platform for studying the engineering layer around algorithmic trading systems. The project is not only about producing signals. Its focus is the infrastructure around them: message transport, typed contracts, reproducible simulations, risk checks, state management, telemetry, dashboards, and test coverage.

The previous public README was accidentally replaced with unrelated Qwen/VAD content. This file restores the correct project identity while omitting private configuration and operational instructions.

## Key Features

### Market Data and Simulation

- Market data ingestion layer.
- Historical simulation path.
- CSV-based data fallback for reproducible experiments.
- Calendar-aware handling of market sessions.
- Reproducible runs with persisted outputs.

### Event-Driven Architecture

- Service-oriented architecture in Python.
- Redis-based message transport.
- Typed events and shared data models.
- Independent services for ingestion, signal generation, risk checks, simulation, API access, and monitoring.
- FakeRedis support for tests and local development.

### Risk and State Management

- Risk validation layer.
- Position and exposure limits.
- Circuit-breaker style controls.
- Persistent deduplication.
- Order-state tracking through a finite-state model.
- Reproducibility checks using persisted run artifacts.

### Backtesting and Analysis

- Backtesting workflow for strategy experiments.
- Historical replay through the system pipeline.
- Exportable results.
- Metrics such as returns, drawdown, win rate, and risk-adjusted performance.
- SHA256-based checks for reproducibility.

### Monitoring

- REST API for system inspection.
- WebSocket dashboard updates.
- Prometheus metrics.
- Grafana dashboards.
- Health checks and service status views.

## Architecture

```text
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Grafana       │    │   Prometheus    │    │    Redis        │
│   Dashboard     │◄───┤   Metrics       │◄───┤   Message Bus   │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                                                       ▲
                                                       │
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│      API        │    │  Risk Manager   │    │  Data Ingestor  │
│  Monitoring     │◄───┤  Validation     │◄───┤  Market Data    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         ▲                       ▲                       ▲
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                    ┌─────────────────┐    ┌─────────────────┐
                    │   Strategies    │    │    Executor     │
                    │   Signals       │    │  State Mgmt     │
                    └─────────────────┘    └─────────────────┘
                                 │
                    ┌─────────────────┐    ┌─────────────────┐
                    │   Simulator     │    │   Backtester    │
                    │   Historical    │    │   Experiments   │
                    └─────────────────┘    └─────────────────┘
```

## System Components

### Data Ingestor

Collects and normalizes market data for the rest of the platform.

### Historical Simulator

Replays historical data so the same pipeline can be tested without relying on a live environment.

### Strategy Layer

Generates structured signal objects for experiments and baseline comparison.

### Risk Manager

Applies validation before downstream state changes. This includes market-session checks, limits, deduplication, and emergency-control logic.

### Executor Layer

Tracks state transitions for order-like objects and records lifecycle events.

### API Service

Exposes status, health, monitoring, and backtest-management surfaces.

### Message Bus

Coordinates communication between services using Redis-backed transport, with fallback support for testing.

## Project Structure

```text
algorithmic-trading-platform/
├── apps/                   # Services
│   ├── api/                # REST API
│   ├── data_ingestor/      # Market data ingestion
│   ├── strategies/         # Signal generation
│   ├── risk_manager/       # Risk controls
│   ├── executor/           # State tracking
│   └── simulator/          # Historical simulation
├── lib/                    # Shared libraries
│   ├── models.py           # Data models
│   ├── bus.py              # Message bus
│   ├── settings.py         # Configuration
│   └── time_utils.py       # Time utilities
├── scripts/                # Management and analysis scripts
├── data/csv/               # Local CSV data files
├── logs/                   # Service logs
├── out/                    # Results and run artifacts
└── configs/                # Configuration files
```

## Testing and Validation

The repository includes tests for:

- Message bus behavior.
- Idempotency.
- Risk checks.
- Market-hours validation.
- Persistence and reproducibility.
- Backtest API behavior.
- Edge cases.
- Load and performance behavior.
- End-to-end pipeline checks.
- Metrics validation.

## Monitoring Surface

The platform includes instrumentation for:

- Signal flow.
- Risk checks.
- State transitions.
- Stream length and consumer lag.
- Processing latency.
- Service uptime.
- Backtest job state.

## Development Notes

For implementation milestones and detailed development notes, see:

- `DEVELOPMENT_HISTORY.md`
- `FIXES_SUMMARY.md`

## Public README Redaction

This public README intentionally omits:

- API keys or credential examples.
- Local secrets.
- Environment-specific setup commands.
- Command sequences for running live trading workflows.
- Personal environment details.

The goal is to describe the software architecture and engineering work behind ALPACA without exposing private configuration or encouraging direct trading use.

## License

See the repository license for details.
