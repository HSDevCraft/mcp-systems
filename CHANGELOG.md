# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- Initial project scaffold

---

## [0.1.0] — 2025-01-01

### Added
- Core MCP engine with Context Manager, Orchestrator, and Module Registry
- Tiered memory system: Working (in-process), Short-term (Redis), Long-term (Qdrant)
- Plugin/module system with interface contracts and lifecycle hooks
- FastAPI gateway with REST endpoints for contexts, memory, and modules
- JWT and API key authentication middleware
- Rate limiting with sliding window per API key
- Structured JSON logging via `structlog`
- Prometheus metrics exposure at `/metrics`
- OpenTelemetry tracing support (opt-in)
- Docker Compose stack (API + Redis + Qdrant + Prometheus + Grafana)
- Kubernetes deployment manifests
- GitHub Actions CI (lint + type-check + test + docker build)
- GitHub Actions release workflow (semantic versioning + PyPI publish stub)
- Full test suite: unit tests for all core modules + integration end-to-end
- Example modules: `echo`, `text-summarizer`, `memory-retriever`
- Documentation: 9 deep-dive docs covering all subsystems
- `Makefile` with developer ergonomics targets
- `pyproject.toml` with full Ruff, Black, mypy, pytest, coverage config

[Unreleased]: https://github.com/your-org/mcp-system/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/your-org/mcp-system/releases/tag/v0.1.0
