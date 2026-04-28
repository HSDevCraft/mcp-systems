# 14 — Testing MCP Servers

Testing MCP servers requires three levels: unit tests (pure logic), integration tests
(in-process server+client), and end-to-end tests (real subprocess). This document covers all three.

---

## Testing Stack

```
pytest                       ← test runner
pytest-asyncio               ← async test support
mcp                          ← in-memory transport for integration tests
pytest-mock / unittest.mock  ← mocking external dependencies
httpx                        ← SSE/HTTP server testing
```

---

## Level 1 — Unit Tests (Pure Logic)

Test tool and resource handler functions in isolation, without any MCP protocol overhead.

```python
# tests/unit/test_tools.py
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Import the raw handler functions (not via MCP)
from my_server import execute_calculator, execute_search, execute_read_file

# ── Calculator ────────────────────────────────────────────────────────────────
class TestCalculator:
    def test_add(self):
        result = execute_calculator("2 + 2")
        assert result == "4"

    def test_float_result(self):
        result = execute_calculator("10 / 3")
        assert "3.33" in result

    def test_invalid_expression(self):
        with pytest.raises(ValueError, match="Invalid expression"):
            execute_calculator("__import__('os').system('rm -rf /')")

    def test_division_by_zero(self):
        result = execute_calculator("1 / 0")
        assert "error" in result.lower() or "division" in result.lower()

# ── File reading ──────────────────────────────────────────────────────────────
class TestReadFile:
    def test_read_existing_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = execute_read_file(str(f), allowed_root=tmp_path)
        assert result == "hello world"

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            execute_read_file(str(tmp_path / "missing.txt"), allowed_root=tmp_path)

    def test_path_traversal_blocked(self, tmp_path):
        with pytest.raises(PermissionError):
            execute_read_file("../../etc/passwd", allowed_root=tmp_path)

# ── Search (mocked HTTP) ──────────────────────────────────────────────────────
class TestSearch:
    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        mock_response = {
            "web": {"results": [
                {"title": "MCP Docs", "url": "https://example.com", "description": "The MCP spec"},
            ]}
        }
        with patch("my_server.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=AsyncMock(json=lambda: mock_response, raise_for_status=lambda: None)
            )
            result = await execute_search("MCP protocol", max_results=1)
        assert "MCP Docs" in result
        assert "https://example.com" in result

    @pytest.mark.asyncio
    async def test_search_no_results(self):
        with patch("my_server.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=AsyncMock(json=lambda: {"web": {"results": []}}, raise_for_status=lambda: None)
            )
            result = await execute_search("xyzzy12345")
        assert "No results" in result
```

---

## Level 2 — Integration Tests (In-Memory Transport)

Test the full MCP protocol flow using the SDK's in-memory transport. No subprocesses, no HTTP.

```python
# tests/integration/test_server_integration.py
import pytest
import pytest_asyncio
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

from my_server import build_server  # function that returns a configured Server instance

@pytest_asyncio.fixture
async def client():
    """Start the server and return a connected client session."""
    server = build_server()
    async with create_connected_server_and_client_session(server) as session:
        yield session

# ── Tool discovery ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_list_tools(client):
    result = await client.list_tools()
    tool_names = [t.name for t in result.tools]
    assert "calculator" in tool_names
    assert "read_file" in tool_names

@pytest.mark.asyncio
async def test_tool_has_schema(client):
    result = await client.list_tools()
    calc = next(t for t in result.tools if t.name == "calculator")
    assert calc.description
    assert calc.inputSchema["type"] == "object"
    assert "expression" in calc.inputSchema["properties"]

# ── Tool calls ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_calculator_add(client):
    result = await client.call_tool("calculator", {"expression": "2 + 2"})
    assert not result.isError
    assert result.content[0].text == "4"

@pytest.mark.asyncio
async def test_calculator_error_response(client):
    result = await client.call_tool("calculator", {"expression": "1 / 0"})
    assert result.isError
    assert result.content[0].text  # has error message

@pytest.mark.asyncio
async def test_unknown_tool_raises(client):
    from mcp.shared.exceptions import McpError
    with pytest.raises(McpError):
        await client.call_tool("nonexistent_tool", {})

# ── Resource discovery ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_list_resources(client):
    result = await client.list_resources()
    assert isinstance(result.resources, list)

@pytest.mark.asyncio
async def test_read_resource(client, tmp_path, monkeypatch):
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello from test")
    monkeypatch.setenv("WORKSPACE", str(tmp_path))

    result = await client.read_resource(f"file://{test_file}")
    assert result.contents[0].text == "hello from test"

# ── Prompt discovery ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_list_prompts(client):
    result = await client.list_prompts()
    prompt_names = [p.name for p in result.prompts]
    assert "code_review" in prompt_names

@pytest.mark.asyncio
async def test_get_prompt_with_args(client):
    result = await client.get_prompt(
        "code_review",
        {"code": "def f(): pass", "language": "python"}
    )
    assert result.messages
    assert len(result.messages) >= 1
    assert "python" in result.messages[0].content.text.lower()

# ── Capabilities ───────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_server_capabilities(client):
    # ClientSession stores init result
    caps = client._initialized_result.capabilities
    assert caps.tools is not None
    assert caps.resources is not None
```

---

## Level 3 — End-to-End Tests (Real Subprocess)

Test against a real server running as a subprocess — same as Claude Desktop would use.

```python
# tests/e2e/test_stdio_subprocess.py
import pytest
import pytest_asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import sys

@pytest_asyncio.fixture
async def real_server():
    """Connect to the server as a real subprocess."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["my_server.py"],
        env={"LOG_LEVEL": "ERROR"},
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            yield session

@pytest.mark.asyncio
@pytest.mark.e2e
async def test_e2e_calculator(real_server):
    result = await real_server.call_tool("calculator", {"expression": "100 * 2"})
    assert not result.isError
    assert "200" in result.content[0].text

@pytest.mark.asyncio
@pytest.mark.e2e
async def test_e2e_server_info(real_server):
    info = real_server._initialized_result.serverInfo
    assert info.name
    assert info.version
```

---

## Mocking External Dependencies

### Mock HTTP calls
```python
import pytest
from unittest.mock import patch, AsyncMock
import httpx

@pytest.fixture
def mock_http():
    with patch("my_server.httpx.AsyncClient") as mock:
        yield mock

@pytest.mark.asyncio
async def test_search_with_mock(client, mock_http):
    mock_http.return_value.__aenter__.return_value.get = AsyncMock(
        return_value=httpx.Response(200, json={"web": {"results": [
            {"title": "T1", "url": "http://a.com", "description": "D1"}
        ]}})
    )
    result = await client.call_tool("web_search", {"query": "test"})
    assert not result.isError
    assert "T1" in result.content[0].text
```

### Mock database
```python
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock

@pytest_asyncio.fixture
async def mock_db():
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__  = AsyncMock(return_value=None)
    conn.fetchrow.return_value = {"id": 1, "name": "Alice", "email": "alice@example.com"}
    return pool

@pytest.mark.asyncio
async def test_get_user(client, mock_db, monkeypatch):
    monkeypatch.setattr("my_server.db_pool", mock_db)
    result = await client.call_tool("get_user", {"user_id": "1"})
    assert not result.isError
    assert "Alice" in result.content[0].text
```

### Mock filesystem
```python
@pytest.mark.asyncio
async def test_read_file(client, tmp_path, monkeypatch):
    monkeypatch.setattr("my_server.WORKSPACE", tmp_path)
    (tmp_path / "notes.txt").write_text("Meeting at 3pm")

    result = await client.call_tool("read_file", {"path": "notes.txt"})
    assert "Meeting at 3pm" in result.content[0].text
```

---

## Testing Notifications

```python
import asyncio
from mcp import ClientSession

@pytest.mark.asyncio
async def test_resource_change_notification():
    notifications = []

    server = build_server()
    async with create_connected_server_and_client_session(server) as session:
        original_handler = session.on_notification
        async def capture(notif):
            notifications.append(notif)
            if original_handler:
                await original_handler(notif)
        session.on_notification = capture

        # Trigger a resource change server-side
        await trigger_resource_change(server, "file:///workspace/test.txt")
        await asyncio.sleep(0.1)  # let notifications propagate

    assert any(
        hasattr(n, "params") and n.params.uri == "file:///workspace/test.txt"
        for n in notifications
    )
```

---

## pytest Configuration

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode      = "auto"
testpaths         = ["tests"]
markers           = [
    "unit: pure unit tests (fast, no I/O)",
    "integration: in-process MCP tests",
    "e2e: real subprocess tests (slow)",
]
filterwarnings    = ["ignore::DeprecationWarning"]

[tool.pytest.ini_options.asyncio_mode]
# or in pytest.ini:
# asyncio_mode = "auto"
```

```ini
# pytest.ini  (alternative)
[pytest]
asyncio_mode = auto
markers =
    unit: fast unit tests
    integration: in-process MCP integration tests
    e2e: slow end-to-end subprocess tests
```

### Running tests
```bash
# Unit tests only (fastest)
pytest tests/unit -v

# Integration tests
pytest tests/integration -v

# All except e2e
pytest -m "not e2e" -v

# Full suite with coverage
pytest --cov=my_server --cov-report=term-missing -v

# Specific test
pytest tests/integration/test_server_integration.py::TestCalculator::test_add -v
```

---

## Test Fixtures Reference

```python
# conftest.py — shared fixtures
import pytest
import pytest_asyncio
from mcp.shared.memory import create_connected_server_and_client_session
from my_server import build_server

@pytest_asyncio.fixture(scope="function")
async def client():
    """Fresh MCP client for each test."""
    async with create_connected_server_and_client_session(build_server()) as session:
        yield session

@pytest_asyncio.fixture(scope="session")
async def shared_client():
    """Shared client for expensive tests (one server for all tests in session)."""
    async with create_connected_server_and_client_session(build_server()) as session:
        yield session

@pytest.fixture
def workspace(tmp_path):
    """Temporary workspace directory."""
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("# Test Project")
    return tmp_path
```

---

## Property-Based Testing

```python
from hypothesis import given, strategies as st

@given(
    a=st.integers(min_value=-1000, max_value=1000),
    b=st.integers(min_value=-1000, max_value=1000),
)
@pytest.mark.asyncio
async def test_calculator_add_properties(client, a, b):
    result = await client.call_tool("calculator", {"expression": f"{a} + {b}"})
    assert not result.isError
    assert str(a + b) in result.content[0].text
```

---

## Contract Testing

Verify that your server conforms to the MCP protocol contract:

```python
# tests/contract/test_mcp_contract.py
"""
Contract tests verify that a server correctly implements the MCP protocol spec.
These tests can be run against ANY MCP server — they are server-agnostic.
"""
import pytest
import pytest_asyncio
from mcp.shared.memory import create_connected_server_and_client_session
from my_server import build_server

@pytest_asyncio.fixture
async def client():
    async with create_connected_server_and_client_session(build_server()) as session:
        yield session

# ── Capability contract ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_initialize_returns_required_fields(client):
    """Server must return serverInfo and protocolVersion."""
    result = client._initialized_result
    assert result.serverInfo is not None
    assert result.serverInfo.name, "serverInfo.name must not be empty"
    assert result.serverInfo.version, "serverInfo.version must not be empty"
    assert result.protocolVersion == "2024-11-05", "Must negotiate current protocol version"

@pytest.mark.asyncio
async def test_tools_list_schema_valid(client):
    """Every tool must have name, description, and valid JSON Schema inputSchema."""
    if not client._initialized_result.capabilities.tools:
        pytest.skip("Server does not expose tools")

    result = await client.list_tools()
    for tool in result.tools:
        assert tool.name, f"Tool missing name: {tool}"
        assert tool.description, f"Tool missing description: {tool.name}"
        assert isinstance(tool.inputSchema, dict), f"inputSchema must be dict: {tool.name}"
        assert tool.inputSchema.get("type") == "object", \
            f"inputSchema must be type=object: {tool.name}"

@pytest.mark.asyncio
async def test_tool_unknown_name_raises_protocol_error(client):
    """Calling unknown tool must raise McpError, not return isError=True."""
    from mcp.shared.exceptions import McpError
    if not client._initialized_result.capabilities.tools:
        pytest.skip("Server does not expose tools")

    with pytest.raises(McpError) as exc_info:
        await client.call_tool("__nonexistent_tool_xyz__", {})
    assert exc_info.value.error.code in (-32602, -32601), \
        "Unknown tool must return INVALID_PARAMS or METHOD_NOT_FOUND"

@pytest.mark.asyncio
async def test_resources_list_has_uri_and_name(client):
    """Every resource must have uri and name."""
    if not client._initialized_result.capabilities.resources:
        pytest.skip("Server does not expose resources")

    result = await client.list_resources()
    for resource in result.resources:
        assert resource.uri,  f"Resource missing uri: {resource}"
        assert resource.name, f"Resource missing name: {resource.uri}"

@pytest.mark.asyncio
async def test_prompts_list_has_name_and_description(client):
    """Every prompt must have name and description."""
    if not client._initialized_result.capabilities.prompts:
        pytest.skip("Server does not expose prompts")

    result = await client.list_prompts()
    for prompt in result.prompts:
        assert prompt.name, f"Prompt missing name: {prompt}"
        assert prompt.description, f"Prompt missing description: {prompt.name}"

@pytest.mark.asyncio
async def test_ping_responds(client):
    """Server must respond to ping within 5 seconds."""
    import asyncio
    await asyncio.wait_for(client.send_ping(), timeout=5.0)

@pytest.mark.asyncio
async def test_pagination_cursor_works(client):
    """Pagination must work: list with cursor should return next page."""
    if not client._initialized_result.capabilities.tools:
        pytest.skip("Server does not expose tools")

    first_page = await client.list_tools()
    if first_page.nextCursor:
        second_page = await client.list_tools(cursor=first_page.nextCursor)
        first_names  = {t.name for t in first_page.tools}
        second_names = {t.name for t in second_page.tools}
        # Second page should not repeat items from first page
        overlap = first_names & second_names
        assert not overlap, f"Pagination returned duplicate tools: {overlap}"
```

---

## Performance Testing

Measure latency and throughput of your MCP server:

```python
# tests/performance/test_throughput.py
import asyncio, time, statistics
import pytest
import pytest_asyncio
from mcp.shared.memory import create_connected_server_and_client_session
from my_server import build_server

@pytest_asyncio.fixture(scope="module")
async def shared_client():
    """Reuse one session for all perf tests (avoid init overhead per test)."""
    async with create_connected_server_and_client_session(build_server()) as session:
        yield session

@pytest.mark.asyncio
@pytest.mark.performance
async def test_tool_latency_p99(shared_client):
    """p99 latency for a simple tool call should be < 50ms."""
    N = 100
    latencies = []

    for _ in range(N):
        start = time.perf_counter()
        result = await shared_client.call_tool("calculator", {"expression": "2+2"})
        elapsed = (time.perf_counter() - start) * 1000  # ms
        latencies.append(elapsed)
        assert not result.isError

    latencies.sort()
    p50 = latencies[N // 2]
    p99 = latencies[int(N * 0.99)]

    print(f"\nLatency (N={N}): p50={p50:.1f}ms  p99={p99:.1f}ms  max={latencies[-1]:.1f}ms")
    assert p99 < 50, f"p99 latency {p99:.1f}ms exceeds 50ms SLO"


@pytest.mark.asyncio
@pytest.mark.performance
async def test_concurrent_tool_calls(shared_client):
    """100 concurrent tool calls should all complete in < 2 seconds."""
    CONCURRENCY = 100

    async def single_call(i: int) -> float:
        start = time.perf_counter()
        await shared_client.call_tool("calculator", {"expression": f"{i} + {i}"})
        return time.perf_counter() - start

    start = time.perf_counter()
    latencies = await asyncio.gather(*[single_call(i) for i in range(CONCURRENCY)])
    total = time.perf_counter() - start

    print(f"\nConcurrent ({CONCURRENCY}): total={total:.2f}s  "
          f"mean={statistics.mean(latencies)*1000:.1f}ms  "
          f"max={max(latencies)*1000:.1f}ms")

    assert total < 2.0, f"100 concurrent calls took {total:.2f}s (limit 2s)"
    assert max(latencies) < 1.0, f"Slowest call took {max(latencies):.2f}s"


@pytest.mark.asyncio
@pytest.mark.performance
async def test_throughput_rps(shared_client):
    """Measure sustained requests per second."""
    DURATION = 5.0  # seconds
    count    = 0
    start    = time.perf_counter()

    while time.perf_counter() - start < DURATION:
        await shared_client.call_tool("calculator", {"expression": "1+1"})
        count += 1

    elapsed = time.perf_counter() - start
    rps = count / elapsed
    print(f"\nThroughput: {rps:.1f} requests/second over {elapsed:.1f}s")
    assert rps > 50, f"Throughput {rps:.1f} RPS is below 50 RPS minimum"
```

---

## Chaos / Fault Injection Testing

Test resilience under failure conditions:

```python
# tests/chaos/test_resilience.py
import asyncio, pytest, pytest_asyncio
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
@pytest.mark.chaos
async def test_tool_timeout_handled_gracefully(client):
    """Tool should return isError when underlying operation times out."""
    with patch("my_server.fetch_data", side_effect=asyncio.TimeoutError):
        result = await client.call_tool("fetch_data_tool", {"url": "https://example.com"})
    assert result.isError
    assert "timeout" in result.content[0].text.lower()


@pytest.mark.asyncio
@pytest.mark.chaos
async def test_db_connection_lost_recovers(client):
    """Server should handle DB connection loss gracefully."""
    async def failing_db(*args, **kwargs):
        raise ConnectionError("Database connection lost")

    with patch("my_server.get_db", new_callable=AsyncMock) as mock_db:
        mock_db.side_effect = failing_db
        result = await client.call_tool("get_user", {"user_id": "123"})

    assert result.isError
    assert any(word in result.content[0].text.lower() for word in ["unavailable", "error", "connection"])


@pytest.mark.asyncio
@pytest.mark.chaos
async def test_external_api_5xx_handled(client):
    """Tool should handle external API 500 errors gracefully."""
    import httpx
    with patch("my_server.httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=httpx.Response(503, text="Service Unavailable")
        )
        result = await client.call_tool("web_search", {"query": "test"})
    assert result.isError


@pytest.mark.asyncio
@pytest.mark.chaos
async def test_malformed_input_never_crashes_server(client):
    """Any input — even malformed — should return error, not crash server."""
    malformed_inputs = [
        {},  # empty args
        {"expression": None},
        {"expression": ""},
        {"expression": "x" * 100_000},  # very long input
        {"expression": "\x00\xff\xfe"},  # binary junk
        {"unknown_key": "value"},
    ]
    for args in malformed_inputs:
        try:
            result = await client.call_tool("calculator", args)
            # Either succeeds or returns soft error — both are fine
            assert isinstance(result.isError, bool)
        except Exception as e:
            # Protocol-level errors are acceptable; crashes are not
            from mcp.shared.exceptions import McpError
            assert isinstance(e, McpError), f"Unexpected crash with args={args}: {e}"


@pytest.mark.asyncio
@pytest.mark.chaos
async def test_server_survives_100_rapid_calls(client):
    """Server should remain functional after 100 rapid concurrent calls."""
    results = await asyncio.gather(*[
        client.call_tool("calculator", {"expression": f"{i}*{i}"})
        for i in range(100)
    ], return_exceptions=True)

    successful = sum(1 for r in results if not isinstance(r, Exception))
    assert successful >= 90, f"Too many failures: {100 - successful}/100 failed"

    # Verify server is still responsive after the storm
    final_result = await client.call_tool("calculator", {"expression": "1+1"})
    assert not final_result.isError
```

---

## Test Coverage Strategy

### Coverage configuration

```toml
# pyproject.toml
[tool.coverage.run]
source         = ["my_server", "src"]
omit           = ["tests/*", "**/__pycache__/*"]
branch         = true   # measure branch coverage, not just line coverage

[tool.coverage.report]
fail_under     = 85     # fail CI if coverage drops below 85%
show_missing   = true
skip_covered   = false
exclude_lines  = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "@overload",
    "raise NotImplementedError",
]
```

### Coverage targets by category

| Test Type | Coverage Target | Command |
|-----------|----------------|---------|
| Unit tests | ≥ 90% | `pytest tests/unit --cov=my_server` |
| Integration tests | ≥ 80% | `pytest tests/integration --cov=my_server` |
| Security tests | All attack vectors | `pytest tests/security -v` |
| Contract tests | All protocol methods | `pytest tests/contract -v` |
| E2E tests | Core user journeys | `pytest tests/e2e -m e2e` |

### Running tests in CI tiers

```bash
# Fast tier (< 30s) — run on every push
pytest tests/unit -x -q

# Integration tier (< 2min) — run on PR
pytest tests/unit tests/integration tests/contract -v

# Full tier (< 10min) — run on main branch
pytest --cov=my_server --cov-report=html -v

# Security tier — run weekly or on dependency updates
pytest tests/security -v --tb=short

# Performance tier — run on performance-sensitive changes
pytest tests/performance -v -m performance
```

---

## Test Utilities Reference

```python
# tests/utils.py — shared test helpers

import asyncio
from contextlib import asynccontextmanager
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

@asynccontextmanager
async def quick_client(server_factory):
    """One-liner client factory for ad-hoc tests."""
    async with create_connected_server_and_client_session(server_factory()) as session:
        yield session

async def assert_tool_succeeds(client: ClientSession, name: str, args: dict) -> str:
    """Assert tool call succeeds and return text content."""
    result = await client.call_tool(name, args)
    assert not result.isError, f"Expected success, got error: {result.content[0].text}"
    return "\n".join(c.text for c in result.content if hasattr(c, "text"))

async def assert_tool_fails(client: ClientSession, name: str, args: dict) -> str:
    """Assert tool call returns isError=True and return error message."""
    result = await client.call_tool(name, args)
    assert result.isError, f"Expected failure, got success: {result.content[0].text}"
    return result.content[0].text

async def collect_notifications(client: ClientSession, action, timeout: float = 1.0) -> list:
    """Run action and collect all notifications emitted within timeout."""
    notifications = []
    original = client.on_notification

    async def capture(n):
        notifications.append(n)
        if original:
            await original(n)

    client.on_notification = capture
    await action()
    await asyncio.sleep(timeout)
    client.on_notification = original
    return notifications
```

---

## Common Testing Pitfalls

| Pitfall | Problem | Fix |
|---------|---------|-----|
| Sharing client session across tests | State leaks between tests | Use `function`-scoped fixtures |
| Not testing `isError` path | Error handling never exercised | Add negative test cases for every tool |
| Missing contract tests | Protocol violations ship to production | Run contract tests on every build |
| No performance tests | Latency regressions go unnoticed | Add p99 latency assertions for core tools |
| Testing implementation details | Tests break on refactor | Test observable behaviour: inputs → outputs |
| Mocking too aggressively | Tests pass but real integration fails | Add integration tests with real dependencies |
| No chaos tests | Server crashes on unexpected inputs | Fuzz inputs; test with DB/API failures |

---

## Key Takeaways

- Use **three test levels**: unit (fast), integration (in-process), E2E (subprocess) — run in order.
- **Contract tests** verify your server is protocol-compliant — run them on every server you build.
- **Property-based testing** (Hypothesis) finds edge cases you wouldn't think to write manually.
- **Performance tests** with p50/p99/throughput measurements catch latency regressions before production.
- **Chaos tests** verify the server remains functional under failure conditions — never silently crashes.
- Target **85%+ branch coverage** with `fail_under` in `pyproject.toml` to enforce it in CI.
