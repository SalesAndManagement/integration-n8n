"""Telegram-шар: команди, whitelist, індикатор роботи, розбиття довгих відповідей."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .agent import AgentReply, ClaudeAgent
from .config import Settings

log = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
CHUNK_SIZE = 3800
TYPING_REFRESH_S = 5.0

HELP_TEXT = (
    "Я агент на Claude Agent SDK, який крутиться на твоєму сервері.\n\n"
    "Просто напиши задачу текстом.\n\n"
    "Команди:\n"
    "/reset — забути контекст цього чату\n"
    "/status — модель, ліміти й дозволені інструменти\n"
    "/help — ця довідка"
)


def split_message(text: str, limit: int = CHUNK_SIZE) -> list[str]:
    """Ріже довгий текст по рядках, щоб влізти в ліміт Telegram."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:  # один наддовгий рядок без переносів
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return [chunk for chunk in (c.strip("\n") for c in chunks) if chunk]


def format_reply(reply: AgentReply, show_trace: bool) -> str:
    """Додає до відповіді короткий підсумок про інструменти й вартість."""
    text = reply.text
    if not show_trace:
        return text

    footer_parts: list[str] = []
    if reply.tools_used:
        seen: list[str] = []
        for name in reply.tools_used:
            if name not in seen:
                seen.append(name)
        footer_parts.append("🔧 " + ", ".join(seen))
    if reply.cost_usd is not None:
        footer_parts.append(f"💲{reply.cost_usd:.4f}")
    if reply.turns:
        footer_parts.append(f"↻{reply.turns}")

    if not footer_parts:
        return text
    return f"{text}\n\n— {' · '.join(footer_parts)}"


async def _keep_typing(bot, chat_id: int) -> None:
    """Тримає індикатор «набирає…», поки агент працює."""
    try:
        while True:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(TYPING_REFRESH_S)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # мережевий збій індикатора не має ламати відповідь
        log.debug("Індикатор набору зупинився: %s", exc)


class TelegramBot:
    def __init__(self, settings: Settings, agent: ClaudeAgent) -> None:
        self._settings = settings
        self._agent = agent

    def build(self) -> Application:
        app = Application.builder().token(self._settings.telegram_token).build()
        allowed = filters.User(user_id=list(self._settings.allowed_user_ids))

        app.add_handler(CommandHandler(["start", "help"], self.help_command, filters=allowed))
        app.add_handler(CommandHandler("reset", self.reset_command, filters=allowed))
        app.add_handler(CommandHandler("status", self.status_command, filters=allowed))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & allowed, self.handle_message))
        # Усе, що не пройшло whitelist.
        app.add_handler(MessageHandler(~allowed, self.reject))
        app.add_error_handler(self.on_error)
        return app

    # --- хендлери ---------------------------------------------------------

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(HELP_TEXT)

    async def reset_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        had_session = self._agent.reset(update.effective_chat.id)
        await update.effective_message.reply_text(
            "Контекст очищено — наступне повідомлення почне нову розмову."
            if had_session
            else "Контексту й не було, розмова вже чиста."
        )

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        settings = self._settings
        session = self._agent.session_id(update.effective_chat.id)
        lines = [
            f"Модель: {settings.model} (effort={settings.effort})",
            f"Ліміти: {settings.max_budget_usd}$ / {settings.max_turns} кроків на запит",
            f"Робочий каталог: {settings.workspace}",
            f"Інструменти: {', '.join(settings.allowed_tools)}",
            f"n8n: {settings.n8n_webhook_base_url or 'не налаштовано'}",
            f"Сесія: {session or 'нова'}",
        ]
        await update.effective_message.reply_text("\n".join(lines))

    async def reject(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        log.warning("Відмова користувачу %s (%s)", user.id if user else "?", user.username if user else "?")
        if update.effective_message:
            await update.effective_message.reply_text("Немає доступу до цього бота.")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat_id = update.effective_chat.id
        prompt = (message.text or "").strip()
        if not prompt:
            return

        typing_task = asyncio.create_task(_keep_typing(context.bot, chat_id))
        try:
            reply = await self._agent.ask(chat_id, prompt)
        except Exception:
            log.exception("Помилка під час обробки запиту в чаті %s", chat_id)
            await message.reply_text("Щось пішло не так під час виконання. Деталі — у логах сервісу.")
            return
        finally:
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task

        for chunk in split_message(format_reply(reply, self._settings.show_tool_trace)):
            await message.reply_text(chunk)

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.exception("Необроблена помилка в Telegram-хендлері", exc_info=context.error)
