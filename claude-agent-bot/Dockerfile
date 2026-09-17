FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # Сесії Claude Code лягають сюди, щоб /reset був єдиним способом втратити контекст
    CLAUDE_CONFIG_DIR=/data/claude \
    AGENT_WORKSPACE=/data/workspace \
    STATE_FILE=/data/sessions.json

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Агент виконує інструменти від свого імені — не даємо йому root.
RUN useradd --create-home --uid 10001 agent \
    && mkdir -p /data/workspace /data/claude \
    && chown -R agent:agent /app /data
USER agent

VOLUME ["/data"]

CMD ["python", "-m", "app.main"]
