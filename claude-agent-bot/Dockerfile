# Node потрібен для Playwright MCP, Python — для самого сервісу.
FROM node:22-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # Браузери ставимо в спільний шлях: збирає root, запускає користувач agent
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    # Сесії Claude Code лягають на том, щоб /reset був єдиним способом втратити контекст
    CLAUDE_CONFIG_DIR=/data/claude \
    AGENT_WORKSPACE=/data/workspace \
    STATE_FILE=/data/sessions.json \
    BROWSER_PROFILE_DIR=/data/browser-profile \
    # Пакет встановлено глобально — npx на старті нічого не тягне з мережі
    BROWSER_MCP_COMMAND=playwright-mcp \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

ARG PLAYWRIGHT_MCP_VERSION=0.0.81

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv "$VIRTUAL_ENV"

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Playwright MCP + chromium тієї самої збірки, яку очікує цей пакет
RUN npm install -g "@playwright/mcp@${PLAYWRIGHT_MCP_VERSION}" \
    && node "$(npm root -g)/@playwright/mcp/node_modules/playwright-core/cli.js" install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

COPY app ./app

# Агент виконує інструменти від свого імені — не даємо йому root.
RUN useradd --create-home --uid 10001 agent \
    && mkdir -p /data/workspace /data/claude /data/browser-profile \
    && chown -R agent:agent /app /data
USER agent

VOLUME ["/data"]

CMD ["python", "-m", "app.main"]
