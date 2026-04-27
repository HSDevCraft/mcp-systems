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
