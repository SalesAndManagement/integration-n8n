"""Перевірки без мережі та без звернень до API: конфіг, режими, браузер, n8n-інструмент."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app.agent import AgentReply, ClaudeAgent
from app.bot import AllowedUser, format_reply, split_message
from app.browser import OUTPUT_SUBDIR, build_playwright_server
from app.config import ConfigError, Settings
from app.tools import _clean_path, build_n8n_server

BASE_ENV = {
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "TELEGRAM_BOT_TOKEN": "123:AA",
    "TELEGRAM_ALLOWED_USER_IDS": "111, 222",
    "N8N_WEBHOOK_BASE_URL": "http://n8n:5678/",
}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Чисте оточення з папкою агента у tmp_path."""
    for key in list(os.environ):
        if key.startswith(("ANTHROPIC_", "TELEGRAM_", "CLAUDE_", "AGENT_", "BROWSER_", "N8N_", "MAX_", "ALLOWED_")):
            monkeypatch.delenv(key, raising=False)
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path / "data" / "workspace"))
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "data" / "sessions.json"))
    return monkeypatch


# --- конфіг ---------------------------------------------------------------


def test_defaults(env):
    s = Settings.from_env()
    assert s.mode == "sandbox" and s.is_sandbox
    assert s.allowed_user_ids == frozenset({111, 222})
    assert s.model == "claude-opus-5"
    assert s.n8n_webhook_base_url == "http://n8n:5678"  # хвостовий слеш прибрано
    assert s.browser_enabled and s.browser_headless and s.browser_persist_profile


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("TELEGRAM_ALLOWED_USER_IDS", ""),  # без whitelist бот не має стартувати
        ("TELEGRAM_ALLOWED_USER_IDS", "не-число"),
        ("AGENT_MODE", "yolo"),
        ("CLAUDE_EFFORT", "turbo"),
        ("MAX_TURNS", "багато"),
    ],
)
def test_bad_config_is_rejected(env, key, value):
    env.setenv(key, value)
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_missing_api_key(env):
    env.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(ConfigError):
        Settings.from_env()


# --- режими ---------------------------------------------------------------


def test_sandbox_mode_gives_every_tool(env):
    options = ClaudeAgent(Settings.from_env())._options(chat_id=1)
    assert options.permission_mode == "bypassPermissions"
    assert options.can_use_tool is None
    assert options.allowed_tools == []
    assert set(options.mcp_servers) == {"n8n", "playwright"}
    assert options.setting_sources == []  # конфіг із ФС не підмішується


def test_restricted_mode_denies_unlisted_tools(env):
    env.setenv("AGENT_MODE", "restricted")
    agent = ClaudeAgent(Settings.from_env())
    options = agent._options(chat_id=1)
    assert options.permission_mode == "default"
    assert options.can_use_tool is not None
    assert "mcp__n8n__trigger_workflow" in options.allowed_tools

    result = asyncio.run(agent._deny_unlisted("Bash", {"command": "rm -rf /"}, None))
    assert result.behavior == "deny"
    assert "Bash" in result.message


def test_limits_are_passed_through(env):
    env.setenv("MAX_BUDGET_USD", "0.25")
    env.setenv("MAX_TURNS", "7")
    options = ClaudeAgent(Settings.from_env())._options(chat_id=1)
    assert options.max_budget_usd == 0.25
    assert options.max_turns == 7


# --- сесії ----------------------------------------------------------------


def test_sessions_survive_restart(env):
    settings = Settings.from_env()
    agent = ClaudeAgent(settings)
    assert agent.session_id(42) is None

    agent._sessions[42] = "sess-abc"
    agent._save_state()

    restarted = ClaudeAgent(settings)
    assert restarted.session_id(42) == "sess-abc"
    assert restarted._options(42).resume == "sess-abc"
    assert restarted.reset(42) is True
    assert restarted.reset(42) is False


def test_corrupt_state_file_does_not_crash(env):
    settings = Settings.from_env()
    settings.state_file.parent.mkdir(parents=True, exist_ok=True)
    settings.state_file.write_text("{зіпсовано", encoding="utf-8")
    assert ClaudeAgent(settings).session_id(1) is None


# --- браузер --------------------------------------------------------------


def test_browser_args(env):
    settings = Settings.from_env()
    config = build_playwright_server(settings)
    assert config["type"] == "stdio" and config["command"] == "npx"

    args = config["args"]
    assert args[:2] == ["-y", settings.browser_mcp_package]
    # Без явного --browser chromium MCP шукає системний Google Chrome і падає.
    assert args[args.index("--browser") + 1] == "chromium"
    assert "--headless" in args and "--no-sandbox" in args and "--isolated" not in args

    output_dir = Path(args[args.index("--output-dir") + 1])
    assert output_dir == settings.workspace / OUTPUT_SUBDIR  # агент читає снапшоти через Read
    assert output_dir.is_dir()
    assert Path(args[args.index("--user-data-dir") + 1]).is_dir()


def test_browser_isolated_and_headed(env):
    env.setenv("BROWSER_PERSIST_PROFILE", "0")
    env.setenv("BROWSER_HEADLESS", "0")
    env.setenv("BROWSER_MCP_COMMAND", "playwright-mcp")
    config = build_playwright_server(Settings.from_env())
    assert config["command"] == "playwright-mcp"
    assert config["args"][0] == "--browser"  # без npx-префікса
    assert "--isolated" in config["args"]
    assert "--user-data-dir" not in config["args"]
    assert "--headless" not in config["args"]


def test_browser_from_local_install(env, tmp_path):
    """Встановлення без root: cli.js лежить у домашній папці, запускаємо через node."""
    cli = tmp_path / "node_modules" / "@playwright" / "mcp" / "cli.js"
    env.setenv("BROWSER_MCP_CLI", str(cli))
    config = build_playwright_server(Settings.from_env())
    assert config["command"] == "node"
    assert config["args"][0] == str(cli)

    # Node теж може бути локальним — тоді запускаємо саме його, а не той, що в PATH
    env.setenv("BROWSER_NODE", str(tmp_path / "vendor" / "node" / "bin" / "node"))
    assert build_playwright_server(Settings.from_env())["command"] == str(
        tmp_path / "vendor" / "node" / "bin" / "node"
    )
    assert "--browser" in config["args"] and "--headless" in config["args"]


def test_browser_env_carries_library_path(env, tmp_path, monkeypatch):
    """Бібліотеки, розпаковані без root, видно тільки через LD_LIBRARY_PATH."""
    libs = str(tmp_path / "vendor" / "syslibs" / "usr" / "lib" / "x86_64-linux-gnu")
    env.setenv("BROWSER_LD_LIBRARY_PATH", libs)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/home/vlad/.cache/ms-playwright")

    server_env = build_playwright_server(Settings.from_env())["env"]
    assert server_env["LD_LIBRARY_PATH"] == libs
    # Якщо цей env замінює оточення, а не доповнює — браузер має лишитись знаходимим.
    assert server_env["PLAYWRIGHT_BROWSERS_PATH"] == "/home/vlad/.cache/ms-playwright"
    assert "PATH" in server_env


def test_browser_env_stays_empty_without_extra_paths(env, monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    server_env = build_playwright_server(Settings.from_env())["env"]
    assert "LD_LIBRARY_PATH" not in server_env


def test_browser_can_be_disabled(env):
    env.setenv("BROWSER_ENABLED", "0")
    options = ClaudeAgent(Settings.from_env())._options(chat_id=1)
    assert set(options.mcp_servers) == {"n8n"}


# --- інструмент n8n -------------------------------------------------------


@pytest.fixture
def fake_n8n(env):
    """Локальний вебхук замість справжнього n8n."""
    received: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"] or 0))
            received.update(path=self.path, body=json.loads(body or "{}"), auth=self.headers.get("Authorization"))
            code = 500 if self.path.endswith("/boom") else 200
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}' if code == 200 else b'{"message":"workflow error"}')

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env.setenv("N8N_WEBHOOK_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    env.setenv("N8N_WEBHOOK_TOKEN", "Bearer secret123")
    yield received
    server.shutdown()


def _trigger_tool(settings):
    """Дістає сам інструмент, бо create_sdk_mcp_server повертає вже готовий конфіг."""
    from app import tools as tools_module

    captured = {}
    original = tools_module.create_sdk_mcp_server

    def capture(name, version="1.0.0", tools=None):
        captured["tools"] = tools
        return original(name=name, version=version, tools=tools)

    tools_module.create_sdk_mcp_server = capture
    try:
        build_n8n_server(settings)
    finally:
        tools_module.create_sdk_mcp_server = original
    return captured["tools"][0]


def test_trigger_workflow_posts_payload(fake_n8n):
    tool = _trigger_tool(Settings.from_env())
    result = asyncio.run(tool.handler({"webhook_path": "/webhook/new-lead", "payload_json": '{"email":"a@b.c"}'}))

    assert not result.get("is_error")
    assert "200" in result["content"][0]["text"]
    assert fake_n8n["path"] == "/webhook/new-lead"
    assert fake_n8n["body"] == {"email": "a@b.c"}
    assert fake_n8n["auth"] == "Bearer secret123"


def test_trigger_workflow_reports_http_error(fake_n8n):
    tool = _trigger_tool(Settings.from_env())
    result = asyncio.run(tool.handler({"webhook_path": "webhook/boom", "payload_json": "{}"}))
    assert result["is_error"] and "500" in result["content"][0]["text"]


def test_trigger_workflow_rejects_bad_input(fake_n8n):
    tool = _trigger_tool(Settings.from_env())
    bad_json = asyncio.run(tool.handler({"webhook_path": "webhook/x", "payload_json": "{не json"}))
    assert bad_json["is_error"] and "JSON" in bad_json["content"][0]["text"]

    # Промпт не має відправляти запити на чужий хост.
    foreign = asyncio.run(tool.handler({"webhook_path": "http://attacker.example/x", "payload_json": "{}"}))
    assert foreign["is_error"]


@pytest.mark.parametrize("bad", ["http://evil/x", "../../etc", "", "a/../../b"])
def test_clean_path_rejects(bad):
    with pytest.raises(ValueError):
        _clean_path(bad)


# --- шар Telegram ---------------------------------------------------------


def test_split_message_respects_limit():
    text = "\n".join(f"рядок {i} " + "x" * 80 for i in range(300))
    chunks = split_message(text)
    assert all(len(chunk) <= 3800 for chunk in chunks)
    assert sum(chunk.count("рядок") for chunk in chunks) == 300


def test_split_message_handles_one_huge_line():
    assert all(len(chunk) <= 3800 for chunk in split_message("y" * 9000))
    assert split_message("коротко") == ["коротко"]


def test_format_reply_footer():
    reply = AgentReply(text="готово", tools_used=["Read", "Read", "Bash"], cost_usd=0.0123, turns=3)
    formatted = format_reply(reply, show_trace=True)
    assert "🔧 Read, Bash" in formatted  # без дублікатів
    assert "0.0123" in formatted
    assert format_reply(reply, show_trace=False) == "готово"


# --- читання .env ---------------------------------------------------------


def test_parse_env_file_handles_real_world_lines():
    from app.env_file import parse_env_file

    parsed = parse_env_file(
        "\n".join(
            [
                "# коментар",
                "",
                "ANTHROPIC_API_KEY=sk-ant-123",
                "export TELEGRAM_BOT_TOKEN=111:AA",
                # значення з пробілами й кирилицею — саме на цьому ламався `source .env`
                "SYSTEM_PROMPT=Ти — робочий асистент. Відповідай стисло.",
                'QUOTED="у лапках"',
                "EMPTY=",
                "BASE_URL=http://n8n:5678/webhook?a=1&b=2",
                "сміття без знаку рівності",
            ]
        )
    )
    assert parsed["ANTHROPIC_API_KEY"] == "sk-ant-123"
    assert parsed["TELEGRAM_BOT_TOKEN"] == "111:AA"
    assert parsed["SYSTEM_PROMPT"] == "Ти — робочий асистент. Відповідай стисло."
    assert parsed["QUOTED"] == "у лапках"
    assert parsed["EMPTY"] == ""
    assert parsed["BASE_URL"] == "http://n8n:5678/webhook?a=1&b=2"
    assert "сміття без знаку рівності" not in parsed


def test_load_env_file_does_not_override_environment(tmp_path, monkeypatch):
    from app.env_file import load_env_file

    env_path = tmp_path / ".env"
    env_path.write_text("FROM_FILE=file\nALREADY_SET=file\n", encoding="utf-8")
    monkeypatch.setenv("ALREADY_SET", "environment")

    assert load_env_file(env_path) == 1
    assert os.environ["FROM_FILE"] == "file"
    # У Docker значення приходять з environment — файл не має їх перетирати.
    assert os.environ["ALREADY_SET"] == "environment"


def test_load_env_file_missing_is_not_an_error(tmp_path):
    from app.env_file import load_env_file

    assert load_env_file(tmp_path / "нема.env") == 0


# --- доступ: id і @username -----------------------------------------------


class _FakeUser:
    def __init__(self, user_id: int, username: str | None = None):
        self.id = user_id
        self.username = username


class _FakeMessage:
    def __init__(self, user):
        self.from_user = user


def test_usernames_are_normalised(env):
    env.setenv("TELEGRAM_ALLOWED_USERNAMES", "@PetrDoroshSM, SM_Vladyslav_Integrator ")
    settings = Settings.from_env()
    # ведуча @ прибрана, регістр знижений — інакше збіг не спрацює
    assert settings.allowed_usernames == frozenset({"petrdoroshsm", "sm_vladyslav_integrator"})


def test_only_usernames_is_enough_to_start(env):
    env.delenv("TELEGRAM_ALLOWED_USER_IDS")
    env.setenv("TELEGRAM_ALLOWED_USERNAMES", "@PetrDoroshSM")
    settings = Settings.from_env()
    assert not settings.allowed_user_ids
    assert settings.allowed_usernames == frozenset({"petrdoroshsm"})


def test_placeholder_in_ids_is_named_in_the_error(env):
    """Найчастіша помилка: у .env лишився текст-заповнювач замість числа."""
    env.setenv("TELEGRAM_ALLOWED_USER_IDS", "твій_id")
    with pytest.raises(ConfigError) as err:
        Settings.from_env()
    message = str(err.value)
    assert "твій_id" in message  # видно, що саме виправляти
    assert "@userinfobot" in message and "TELEGRAM_ALLOWED_USERNAMES" in message


def test_negative_ids_are_accepted(env):
    """У груп і каналів id відʼємні — не приймати їх було б помилкою."""
    env.setenv("TELEGRAM_ALLOWED_USER_IDS", "-1001234567890, 111")
    assert Settings.from_env().allowed_user_ids == frozenset({-1001234567890, 111})


def test_no_ids_and_no_usernames_refuses_to_start(env):
    env.delenv("TELEGRAM_ALLOWED_USER_IDS")
    env.setenv("TELEGRAM_ALLOWED_USERNAMES", "")
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_allowed_user_filter(env):
    env.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    env.setenv("TELEGRAM_ALLOWED_USERNAMES", "@PetrDoroshSM")
    allowed = AllowedUser(Settings.from_env())

    assert allowed.filter(_FakeMessage(_FakeUser(111)))  # за id
    # Telegram віддає username так, як його зареєстровано — регістр не має вирішувати
    assert allowed.filter(_FakeMessage(_FakeUser(999, "PetrDoroshSM")))
    assert allowed.filter(_FakeMessage(_FakeUser(999, "petrdoroshsm")))

    assert not allowed.filter(_FakeMessage(_FakeUser(999, "stranger")))
    assert not allowed.filter(_FakeMessage(_FakeUser(999, None)))  # username може не бути
    assert not allowed.filter(_FakeMessage(None))


# --- помилки видно в чаті ---------------------------------------------------


def test_ask_wraps_failures_with_stderr(env, monkeypatch):
    """Причина падіння живе в stderr процесу Claude Code — вона має дійти до чату."""
    from app import agent as agent_module

    agent = ClaudeAgent(Settings.from_env())

    def exploding_query(prompt, options):
        options.stderr("Error: connect ECONNREFUSED 127.0.0.1:443")
        options.stderr("")

        async def gen():
            raise RuntimeError("boom")
            yield  # pragma: no cover

        return gen()

    monkeypatch.setattr(agent_module, "query", exploding_query)

    with pytest.raises(agent_module.AgentError) as err:
        asyncio.run(agent.ask(1, "привіт"))

    text = str(err.value)
    assert "RuntimeError: boom" in text
    assert "ECONNREFUSED" in text  # хвіст stderr прикріплено


def test_describe_names_the_sdk_errors(env):
    from claude_agent_sdk import CLINotFoundError, ProcessError

    from app.agent import AgentError  # noqa: F401  (перевіряємо, що експортується)

    agent = ClaudeAgent(Settings.from_env())

    assert "setup-native.sh" in agent._describe(CLINotFoundError("not found"))

    described = agent._describe(ProcessError("failed", exit_code=1, stderr="Invalid API key"))
    assert "кодом 1" in described and "Invalid API key" in described


# --- ключ поза workspace ----------------------------------------------------


def test_workspace_id_becomes_a_custom_header(env, monkeypatch):
    """Ключ рівня організації без цього заголовка отримує 400 від API."""
    monkeypatch.delenv("ANTHROPIC_CUSTOM_HEADERS", raising=False)
    env.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_123")
    options = ClaudeAgent(Settings.from_env())._options(chat_id=1)
    assert options.env["ANTHROPIC_CUSTOM_HEADERS"] == "anthropic-workspace-id: wrkspc_123"


def test_manual_custom_headers_win(env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "X-Mine: 1")
    env.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_123")
    # Те, що людина вписала руками, не перетираємо.
    assert ClaudeAgent(Settings.from_env())._options(chat_id=1).env == {}


def test_no_workspace_id_no_header(env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_CUSTOM_HEADERS", raising=False)
    assert ClaudeAgent(Settings.from_env())._options(chat_id=1).env == {}
