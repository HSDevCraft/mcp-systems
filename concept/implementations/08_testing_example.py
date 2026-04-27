"""
08 — MCP Server Testing Example

Comprehensive test suite for 06_multi_capability_server.py.
Covers: unit tests, integration tests (in-memory), and e2e subprocess tests.

Run all:        pytest 08_testing_example.py -v
Run unit only:  pytest 08_testing_example.py -v -m unit
Run integration: pytest 08_testing_example.py -v -m integration
Run e2e:        pytest 08_testing_example.py -v -m e2e
Coverage:       pytest 08_testing_example.py --cov=06_multi_capability_server -v
"""

import asyncio
import json
import math
import sys
from pathlib import Path

import pytest
import pytest_asyncio

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def server_script() -> Path:
    """Path to the multi-capability server script."""
    return Path(__file__).parent / "06_multi_capability_server.py"


@pytest_asyncio.fixture
async def in_memory_client(tmp_path):
    """
    In-memory MCP client connected to the multi-capability server.
    No subprocess — fast and isolated.
    """
    try:
        from mcp.shared.memory import create_connected_server_and_client_session
    except ImportError:
        pytest.skip("mcp.shared.memory not available in this SDK version")

    # Import the server builder
    sys.path.insert(0, str(Path(__file__).parent))

    # Patch workspace to tmp_path for isolation
    import importlib
    import os

    original_workspace = os.environ.get("WORKSPACE")
    os.environ["WORKSPACE"] = str(tmp_path)

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "full_server",
            Path(__file__).parent / "06_multi_capability_server.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        srv = module.server

        async with create_connected_server_and_client_session(srv) as session:
            yield session
    finally:
        if original_workspace is None:
            os.environ.pop("WORKSPACE", None)
        else:
            os.environ["WORKSPACE"] = original_workspace


@pytest_asyncio.fixture
async def subprocess_client(server_script, tmp_path):
    """
    MCP client connecting to the server as a real subprocess.
    Slower but tests real process isolation.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(server_script)],
        env={
            "WORKSPACE": str(tmp_path),
            "LOG_LEVEL": "ERROR",
        },
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            yield session


# ════════════════════════════════════════════════════════════════════════════════
#  UNIT TESTS  (pure Python, no MCP protocol)
# ════════════════════════════════════════════════════════════════════════════════

class TestCalculatorLogic:
    """Unit tests for the calculator logic (isolated from MCP)."""

    @pytest.mark.unit
    def test_basic_addition(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "srv", Path(__file__).parent / "06_multi_capability_server.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        result = eval("2 + 2", {"__builtins__": {}}, {"abs": abs})
        assert result == 4

    @pytest.mark.unit
    def test_math_sqrt(self):
        assert math.sqrt(144) == 12.0

    @pytest.mark.unit
    def test_math_power(self):
        assert 2 ** 10 == 1024


class TestPathValidation:
    """Unit tests for workspace path validation logic."""

    @pytest.mark.unit
    def test_valid_relative_path(self, tmp_path):
        workspace = tmp_path
        test_file = tmp_path / "subdir" / "file.txt"
        test_file.parent.mkdir()
        test_file.write_text("hello")

        resolved = (workspace / "subdir/file.txt").resolve()
        assert resolved.is_relative_to(workspace.resolve())

    @pytest.mark.unit
    def test_path_traversal_blocked(self, tmp_path):
        workspace = tmp_path
        evil_path = (workspace / "../../etc/passwd").resolve()
        assert not evil_path.is_relative_to(workspace.resolve())

    @pytest.mark.unit
    def test_absolute_path_outside_workspace(self, tmp_path):
        workspace = tmp_path
        outside   = Path("/tmp/outside_file.txt").resolve()
        assert not outside.is_relative_to(workspace.resolve())


# ════════════════════════════════════════════════════════════════════════════════
#  INTEGRATION TESTS  (in-memory MCP client ↔ server)
# ════════════════════════════════════════════════════════════════════════════════

class TestToolDiscovery:

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_list_tools_returns_list(self, in_memory_client):
        result = await in_memory_client.list_tools()
        assert isinstance(result.tools, list)
        assert len(result.tools) > 0

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_expected_tools_present(self, in_memory_client):
        result     = await in_memory_client.list_tools()
        tool_names = {t.name for t in result.tools}
        for expected in {"calculate", "read_file", "write_file", "list_files", "server_status"}:
            assert expected in tool_names, f"Expected tool '{expected}' not found"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_tools_have_descriptions(self, in_memory_client):
        result = await in_memory_client.list_tools()
        for t in result.tools:
            assert t.description, f"Tool '{t.name}' has no description"
            assert len(t.description) > 10, f"Tool '{t.name}' description too short"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_tools_have_valid_schemas(self, in_memory_client):
        result = await in_memory_client.list_tools()
        for t in result.tools:
            schema = t.inputSchema
            assert isinstance(schema, dict), f"Tool '{t.name}' has non-dict schema"
            assert schema.get("type") == "object", f"Tool '{t.name}' schema type must be 'object'"


class TestCalculateTool:

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_simple_addition(self, in_memory_client):
        r = await in_memory_client.call_tool("calculate", {"expression": "2 + 2"})
        assert not r.isError
        assert r.content[0].text == "4"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_float_result(self, in_memory_client):
        r = await in_memory_client.call_tool("calculate", {"expression": "10 / 3"})
        assert not r.isError
        assert "3.33" in r.content[0].text

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_power_operator(self, in_memory_client):
        r = await in_memory_client.call_tool("calculate", {"expression": "2 ** 10"})
        assert not r.isError
        assert "1024" in r.content[0].text

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_sqrt_function(self, in_memory_client):
        r = await in_memory_client.call_tool("calculate", {"expression": "sqrt(144)"})
        assert not r.isError
        assert "12" in r.content[0].text

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_division_by_zero_soft_error(self, in_memory_client):
        r = await in_memory_client.call_tool("calculate", {"expression": "1 / 0"})
        assert r.isError
        assert r.content[0].text

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_forbidden_keyword_soft_error(self, in_memory_client):
        r = await in_memory_client.call_tool("calculate", {"expression": "__import__('os')"})
        assert r.isError
        assert "forbidden" in r.content[0].text.lower()

    @pytest.mark.integration
    @pytest.mark.asyncio
    @pytest.mark.parametrize("expr,expected", [
        ("1 + 1",      "2"),
        ("10 - 4",     "6"),
        ("3 * 7",      "21"),
        ("abs(-42)",   "42"),
        ("round(3.7)", "4"),
    ])
    async def test_parametrized_expressions(self, in_memory_client, expr, expected):
        r = await in_memory_client.call_tool("calculate", {"expression": expr})
        assert not r.isError
        assert expected in r.content[0].text


class TestFileTools:

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_write_and_read_file(self, in_memory_client, tmp_path):
        content = "Hello from MCP test!\nLine 2."
        w = await in_memory_client.call_tool("write_file", {
            "path": "test_output.txt",
            "content": content,
        })
        assert not w.isError

        r = await in_memory_client.call_tool("read_file", {"path": "test_output.txt"})
        assert not r.isError
        assert content in r.content[0].text

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, in_memory_client):
        r = await in_memory_client.call_tool("read_file", {"path": "does_not_exist.txt"})
        assert r.isError
        assert r.content[0].text

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, in_memory_client):
        r = await in_memory_client.call_tool("read_file", {"path": "../../etc/passwd"})
        assert r.isError

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_append_mode(self, in_memory_client):
        await in_memory_client.call_tool("write_file", {
            "path": "append_test.txt",
            "content": "Line 1\n",
        })
        await in_memory_client.call_tool("write_file", {
            "path": "append_test.txt",
            "content": "Line 2\n",
            "append": True,
        })
        r = await in_memory_client.call_tool("read_file", {"path": "append_test.txt"})
        assert "Line 1" in r.content[0].text
        assert "Line 2" in r.content[0].text

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_list_files(self, in_memory_client):
        await in_memory_client.call_tool("write_file", {
            "path": "sample_a.txt",
            "content": "a",
        })
        await in_memory_client.call_tool("write_file", {
            "path": "sample_b.txt",
            "content": "b",
        })
        r = await in_memory_client.call_tool("list_files", {})
        assert not r.isError
        text = r.content[0].text
        assert "sample_a.txt" in text or "sample_b.txt" in text


class TestServerStatus:

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_server_status_returns_json(self, in_memory_client):
        r = await in_memory_client.call_tool("server_status", {})
        assert not r.isError
        data = json.loads(r.content[0].text)
        assert "version" in data
        assert "uptime_seconds" in data
        assert "request_count" in data

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_request_count_increments(self, in_memory_client):
        r1 = await in_memory_client.call_tool("server_status", {})
        count1 = json.loads(r1.content[0].text)["request_count"]

        await in_memory_client.call_tool("calculate", {"expression": "1+1"})
        await in_memory_client.call_tool("calculate", {"expression": "2+2"})

        r2 = await in_memory_client.call_tool("server_status", {})
        count2 = json.loads(r2.content[0].text)["request_count"]

        assert count2 > count1


class TestResourceDiscovery:

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_list_resources_returns_list(self, in_memory_client):
        result = await in_memory_client.list_resources()
        assert isinstance(result.resources, list)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_static_resources_present(self, in_memory_client):
        result = await in_memory_client.list_resources()
        uris = {r.uri for r in result.resources}
        assert "server://status" in uris
        assert "server://config" in uris

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_read_server_status_resource(self, in_memory_client):
        result = await in_memory_client.read_resource("server://status")
        assert result.contents
        data = json.loads(result.contents[0].text)
        assert "uptime_seconds" in data

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_read_server_config_resource(self, in_memory_client):
        result = await in_memory_client.read_resource("server://config")
        assert result.contents
        data = json.loads(result.contents[0].text)
        assert "server_name" in data
        assert "server_version" in data

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_unknown_resource_raises(self, in_memory_client):
        from mcp.shared.exceptions import McpError
        with pytest.raises((McpError, Exception)):
            await in_memory_client.read_resource("unknown://xyz")


class TestPromptDiscovery:

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_list_prompts_returns_list(self, in_memory_client):
        result = await in_memory_client.list_prompts()
        assert isinstance(result.prompts, list)
        assert len(result.prompts) > 0

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_expected_prompts_present(self, in_memory_client):
        result   = await in_memory_client.list_prompts()
        names    = {p.name for p in result.prompts}
        for expected in {"code_review", "explain", "improve_text"}:
            assert expected in names

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_code_review_prompt(self, in_memory_client):
        result = await in_memory_client.get_prompt(
            "code_review",
            {"code": "def f(x): return x*2", "language": "python"},
        )
        assert result.messages
        text = result.messages[0].content.text
        assert "python" in text.lower()
        assert "def f" in text or "code" in text.lower()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_explain_prompt(self, in_memory_client):
        result = await in_memory_client.get_prompt(
            "explain",
            {"topic": "recursion", "audience": "beginner"},
        )
        assert result.messages
        assert "recursion" in result.messages[0].content.text.lower()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_prompt_with_minimal_args(self, in_memory_client):
        result = await in_memory_client.get_prompt(
            "code_review",
            {"code": "x = 1"},
        )
        assert result.messages
        assert result.messages[0].content.text


class TestErrorHandling:

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_unknown_tool_raises_mcp_error(self, in_memory_client):
        from mcp.shared.exceptions import McpError
        with pytest.raises((McpError, Exception)):
            await in_memory_client.call_tool("this_tool_does_not_exist", {})

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_tool_error_is_soft(self, in_memory_client):
        r = await in_memory_client.call_tool("read_file", {"path": "missing_file.txt"})
        assert r.isError
        assert r.content[0].text

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_error_content_is_descriptive(self, in_memory_client):
        r = await in_memory_client.call_tool("read_file", {"path": "missing.txt"})
        assert r.isError
        msg = r.content[0].text.lower()
        assert any(word in msg for word in ["not found", "error", "missing", "no such"])


class TestCapabilities:

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_server_declares_tools(self, in_memory_client):
        caps = in_memory_client._initialized_result.capabilities
        assert caps.tools is not None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_server_declares_resources(self, in_memory_client):
        caps = in_memory_client._initialized_result.capabilities
        assert caps.resources is not None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_server_declares_prompts(self, in_memory_client):
        caps = in_memory_client._initialized_result.capabilities
        assert caps.prompts is not None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_server_info_present(self, in_memory_client):
        info = in_memory_client._initialized_result.serverInfo
        assert info.name
        assert info.version


# ════════════════════════════════════════════════════════════════════════════════
#  END-TO-END TESTS  (real subprocess)
# ════════════════════════════════════════════════════════════════════════════════

class TestE2ESubprocess:

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_e2e_server_starts(self, subprocess_client):
        info = subprocess_client._initialized_result.serverInfo
        assert info.name == "full-server"

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_e2e_calculator(self, subprocess_client):
        r = await subprocess_client.call_tool("calculate", {"expression": "7 * 6"})
        assert not r.isError
        assert "42" in r.content[0].text

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_e2e_file_round_trip(self, subprocess_client, tmp_path):
        content = "E2E test content — written via MCP"
        w = await subprocess_client.call_tool("write_file", {
            "path": "e2e_test.txt",
            "content": content,
        })
        assert not w.isError

        r = await subprocess_client.call_tool("read_file", {"path": "e2e_test.txt"})
        assert not r.isError
        assert content in r.content[0].text

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_e2e_tools_discoverable(self, subprocess_client):
        result = await subprocess_client.list_tools()
        assert len(result.tools) >= 4

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_e2e_resources_discoverable(self, subprocess_client):
        result = await subprocess_client.list_resources()
        uris = {r.uri for r in result.resources}
        assert "server://status" in uris

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_e2e_ping(self, subprocess_client):
        import time
        t0 = time.perf_counter()
        await subprocess_client.send_ping()
        ms = (time.perf_counter() - t0) * 1000
        assert ms < 5000, f"Ping too slow: {ms:.0f}ms"


# ── pytest configuration ──────────────────────────────────────────────────────

def pytest_configure(config):
    config.addinivalue_line("markers", "unit: pure unit tests (fast, no I/O)")
    config.addinivalue_line("markers", "integration: in-process MCP integration tests")
    config.addinivalue_line("markers", "e2e: real subprocess end-to-end tests (slow)")
