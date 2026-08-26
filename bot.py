"""
Сова — добрый помощник Telegram-группы 🦉
"""

import json
import logging
import os
import random
import threading
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    filters,
)

from config import TELEGRAM_TOKEN, GEMINI_API_KEY, BOT_NAME, SYSTEM_PROMPT


# ─── Веб-сервер для Railway (чтобы не засыпал) ────────────────
class HealthHandler(BaseHTTPRequestHandler):
    """Обработчик health check запросов от UptimeRobot."""
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Sova is alive!")

    def log_message(self, format, *args):
        # Отключаем логи health check чтобы не засорять
        pass


def start_web_server():
    """Запускает простой веб-сервер на порту 8080."""
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info("🌐 Веб-сервер запущен на порту %d", port)
    server.serve_forever()

# ─── Логирование ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Постоянное хранилище истории ─────────────────────────────
HISTORY_DIR = os.path.join(os.path.dirname(__file__), "chat_history")
HISTORY_LEN = 100  # Храним последние 100 сообщений на чат

os.makedirs(HISTORY_DIR, exist_ok=True)

chat_histories: dict[int, list[dict]] = defaultdict(list)


def _history_path(chat_id: int) -> str:
    """Путь к файлу истории чата."""
    return os.path.join(HISTORY_DIR, f"chat_{chat_id}.json")


def load_history(chat_id: int) -> list[dict]:
    """Загружает историю чата из файла."""
    path = _history_path(chat_id)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                logger.info("Загружена история чата %s: %d сообщений", chat_id, len(data))
                return data
        except Exception as e:
            logger.warning("Ошибка загрузки истории чата %s: %s", chat_id, e)
    return []


def save_history(chat_id: int, history: list[dict]) -> None:
    """Сохраняет историю чата в файл."""
    path = _history_path(chat_id)
    # Ограничиваем длину
    trimmed = history[-HISTORY_LEN:]
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(trimmed, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Ошибка сохранения истории чата %s: %s", chat_id, e)


# Загружаем истории при старте
def _load_all_histories() -> None:
    """Загружает все истории из файлов."""
    if not os.path.exists(HISTORY_DIR):
        return
    for filename in os.listdir(HISTORY_DIR):
        if filename.startswith("chat_") and filename.endswith(".json"):
            chat_id = int(filename.replace("chat_", "").replace(".json", ""))
            chat_histories[chat_id] = load_history(chat_id)


# Загружаем при импорте модуля
_load_all_histories()

# ─── Зеркальное время (петарды!) ──────────────────────────────
# Время последней "петарды" по каждой группе
last_firework_time: dict[int, str] = {}  # chat_id -> "YYYY-MM-DD"

MOSCOW_TZ = timezone(timedelta(hours=3))

# Варианты сообщений для зеркального времени
FIREWORK_MESSAGES = [
    "Петарда в зеркальное время! 🎆🔥",
    "Зеркальное время! Пусть будет ярко! 🎇✨",
    "Огоньки! 🔥🔥🔥",
    "Петарды полетели! 🎆💫",
    "Зеркальчик! Пусть искры летят! ✨🎇",
    "Ракета в зеркальное время! 🚀🎆",
    "Бум! Зеркальное время! 💥🎆",
    "Искры! 🔥✨ Петарда!",
]


def is_mirror_time() -> bool:
    """Проверяет, зеркальное ли сейчас время по МСК."""
    now = datetime.now(MOSCOW_TZ)
    h, m = now.hour, now.minute

    # Формат HH:MM — проверяем зеркальность
    time_str = f"{h:02d}{m:02d}"
    return time_str == time_str[::-1]


def already_sent_today(chat_id: int) -> bool:
    """Проверяем, отправляли ли уже петарду сегодня в этот чат."""
    now = datetime.now(MOSCOW_TZ)
    today = now.strftime("%Y-%m-%d")
    return last_firework_time.get(chat_id) == today


async def maybe_send_firework(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """Отправляет петарду в зеркальное время (с вероятностью ~30%)."""
    if not is_mirror_time():
        return

    if already_sent_today(chat_id):
        return

    # 30% шанс отправить петарду в зеркальное время
    if random.random() > 0.3:
        return

    now = datetime.now(MOSCOW_TZ)
    time_str = f"{now.hour:02d}:{now.minute:02d}"

    message = f"🎆🔥 {random.choice(FIREWORK_MESSAGES)} ({time_str} МСК)"
    await context.bot.send_message(chat_id=chat_id, text=message)

    # Запоминаем, что отправили сегодня
    last_firework_time[chat_id] = now.strftime("%Y-%m-%d")
    logger.info("Петарда отправлена в чат %s в %s МСК", chat_id, time_str)

# ─── Шаблоны ответов (fallback без OpenAI) ───────────────────
FRIENDLY_RESPONSES = {
    "greeting": [
        "Привет-привет! 🦉 Как дела, дорогой?",
        "О, привет! Рада тебя видеть! ✨",
        "Приветики! Как настроение? 🌸",
    ],
    "thanks": [
        "Всегда пожалуйста! 🦉💜",
        "Рада помочь! Обращайся ещё ☺️",
        "Не за что, милейший! ✨",
    ],
    "how_are_you": [
        "Сова в деле! 🦉 Как ваши дела?",
        "Отлично, спасибо что спрашиваешь! А у тебя как? 🌟",
        "Бодрствую и готова помогать! 💪🦉",
    ],
    "positive": [
        "Ого, как здорово! 🎉",
        "Какая приятная новость! Рада за тебя! ✨",
        "Вот это класс! Продолжай в том же духе! 💪",
    ],
    "comfort": [
        "Не переживай, всё наладится! 🦉💜",
        "Совушка рядом, не волнуйся! Мы справимся! 💪",
        "Бывает tough, но ты сильнее, чем думаешь! 🌟",
    ],
    "default": [
        "Интересно! Расскажи больше? 🦉",
        "Ого, не знал(а)! Спасибо, что поделился(ась)! ✨",
        "Хм, хороший вопрос! Думаю над этим 🦉",
        "Понимаю! А что думаешь об этом? 💭",
    ],
}


def get_fallback_response(text: str) -> str:
    """Генерирует ответ на основе ключевых слов без Gemini."""
    text_lower = text.lower()

    # Приветствия
    if any(w in text_lower for w in ("привет", "здравствуй", "добрый", "хай", "хей", "hello", "hi")):
        return random.choice(FRIENDLY_RESPONSES["greeting"])

    # Благодарности
    if any(w in text_lower for w in ("спасибо", "благодар", "спс", "thanks", "thank")):
        return random.choice(FRIENDLY_RESPONSES["thanks"])

    # Как дела
    if any(w in text_lower for w in ("как дела", "как ты", "как живёшь", "how are you")):
        return random.choice(FRIENDLY_RESPONSES["how_are_you"])

    # Позитив
    if any(w in text_lower for w in ("ура", "класс", "супер", "отлично", "круто", "здорово", "յay")):
        return random.choice(FRIENDLY_RESPONSES["positive"])

    # Утешение
    if any(w in text_lower for w in (
        "плохо", "грустно", "печально", "тяжело", "сложно",
        "хуже", "ужасно", "стресс", "устал", "нет сил",
    )):
        return random.choice(FRIENDLY_RESPONSES["comfort"])

    return random.choice(FRIENDLY_RESPONSES["default"])


# ─── Google Gemini (бесплатный тариф!) ───────────────────────

# Кэш для модели (создаём один раз)
_gemini_model = None

def _get_gemini_model():
    """Получить или создать модель Gemini (с кэшированием)."""
    global _gemini_model
    if _gemini_model is None and GEMINI_API_KEY:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        _gemini_model = genai.GenerativeModel(
            model_name="gemini-3.5-flash-lite",
            system_instruction=SYSTEM_PROMPT,
        )
    return _gemini_model


async def ask_ai(user_message: str, history: list[dict], sender_name: str) -> str | None:
    """Отправляет запрос в Google Gemini с таймаутом."""
    if not GEMINI_API_KEY:
        return None

    try:
        import asyncio

        model = _get_gemini_model()
        if model is None:
            return None

        # Текущая дата и время по МСК
        now = datetime.now(MOSCOW_TZ)
        date_str = now.strftime("%d.%m.%Y")
        time_str = now.strftime("%H:%M")
        weekday_names = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        weekday = weekday_names[now.weekday()]

        # Формируем историю для Gemini (ограничиваем до 10 сообщений для скорости)
        gemini_history = []
        for msg in history[-10:]:
            role = "user" if msg["role"] == "user" else "model"
            gemini_history.append({"role": role, "parts": [msg["content"]]})

        chat = model.start_chat(history=gemini_history)

        # Добавляем информацию о текущем времени к сообщению
        full_message = f"[Сейчас: {weekday}, {date_str}, {time_str} МСК] [{sender_name}]: {user_message}"

        # Запускаем с таймаутом 20 секунд
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: chat.send_message(full_message)
            ),
            timeout=20  # 20 секунд максимум
        )
        return response.text.strip()
    except asyncio.TimeoutError:
        logger.warning("Gemini: таймаут (15 сек) для сообщения от %s", sender_name)
        return None
    except Exception as e:
        logger.warning("Gemini ошибка: %s", e)
        return None


# ─── Обработчики Telegram ────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /start — приветствие."""
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"Привет, {name}! 🦉 Я Сова — помощница этого чата.\n"
        f"Просто напиши что-нибудь, и я постараюсь поддержать беседу! ✨"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /help."""
    await update.message.reply_text(
        "🦉 Я Сова — добрая помощница чата!\n\n"
        "Что я умею:\n"
        "• Поддерживаю позитивные беседы 💬\n"
        "• Помогаю, когда грустно 💜\n"
        "• Радуюсь, когда весело! 🎉\n"
        "• Отвечаю на вопросы ✨\n\n"
        "Просто пиши — и я отвечу! 😊"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает каждое сообщение в группе."""
    try:
        message = update.message
        if not message:
            return

        chat_id = message.chat_id
        sender = message.from_user
        sender_name = sender.first_name if sender else "Кто-то"

        # ─── Проверяем зеркальное время для петард ─────────────
        await maybe_send_firework(context, chat_id)

        # Получаем текст: из текста или из подписи к картинке/видео
        text = ""
        if message.text:
            text = message.text.strip()
        elif message.caption:
            text = message.caption.strip()

        # Игнорируем пустые и слишком длинные
        if len(text) < 2 or len(text) > 500:
            return

        # ─── Проверяем хештег #обмен (ВСЕГДА, без зависимости от Gemini) ───
        if "#обмен" in text.lower():
            logger.info("Обнаружен #обмен от %s в чате %s", sender_name, chat_id)

            # Случайные варианты поздравления
            exchange_messages = [
                "🎉🏆 ОГО! @{username}, ты собрал(а) КОМБО! Кажется, у нас тут ОБМЕН! 🔄✨\n\n@Kolibry8, АЛЕНА! Срочно сюда! Тут движ! 🦉💫",
                "⚡🔄 УРА! @{username} активировал(а) обмен! 🔥\n\n@Kolibry8, Аленочка, посмотри что тут творится! У нас тут комбо-момент! 🎰✨",
                "🦉✨ УХ-ТЫ! @{username} собрал(а) все фишки и запустил(а) ОБМЕН! 🎰\n\n@Kolibry8, Ален, выхожи! Тут нужен твой экспертный взгляд! 👀💫",
                "🎊🔄 БИНГО! @{username} поймал(а) комбо и требует обмена! 🏅\n\n@Kolibry8, Аленчик, бегом сюда — у нас тут праздничный хаос! 🦉🎉",
                "🚀🔄 Ё-МОЁ! @{username} собрал(а) КОМБО! 🎉\n\n@Kolibry8, Ален, это celebration — тут обмен! Выходи на связь! 📞🦉",
            ]

            response = random.choice(exchange_messages).format(username=sender_name)
            await message.reply_text(response)
            return  # Не обрабатываем дальше

        # Определяем, обращаются ли к боту
        bot_username = context.bot.username
        is_mentioned = (
            f"@{bot_username}" in text
            or BOT_NAME.lower() in text.lower()
            or (message.reply_to_message and message.reply_to_message.from_user.id == context.bot.id)
        )

        # В группе отвечаем только если:
        # 1. Бота упомянули / ответили на его сообщение
        # 2. Это ответ на сообщение бота (чтобы поддерживать диалог)
        if message.chat.type in ("group", "supergroup") and not is_mentioned:
            return

        logger.info("Сообщение от %s в чате %s: %s", sender_name, chat_id, text[:80])

        # Пробуем Gemini
        ai_response = await ask_ai(text, chat_histories[chat_id], sender_name)

        # Если Gemini ответил — отправляем и сохраняем в историю
        if ai_response:
            chat_histories[chat_id].append({
                "role": "user",
                "content": f"[{sender_name}]: {text}",
            })
            chat_histories[chat_id].append({
                "role": "assistant",
                "content": ai_response,
            })
            # Ограничиваем длину истории
            if len(chat_histories[chat_id]) > HISTORY_LEN:
                chat_histories[chat_id] = chat_histories[chat_id][-HISTORY_LEN:]

            # Сохраняем в файл
            save_history(chat_id, chat_histories[chat_id])

            await message.reply_text(ai_response)
        else:
            logger.info("Gemini не ответил для %s — пропускаем", sender_name)

    except Exception as e:
        logger.error("Критическая ошибка в handle_message: %s", e, exc_info=True)
        # Пытаемся отправить сообщение об ошибке
        try:
            if update.message:
                await update.message.reply_text("🦉 Ой, что-то пошло не так... Попробуйте ещё раз!")
        except Exception:
            pass


# ─── Запуск ───────────────────────────────────────────────────

def main() -> None:
    if not TELEGRAM_TOKEN:
        raise RuntimeError(
            "Не задан TELEGRAM_TOKEN!\n"
            "Создай бота через @BotFather и добавь токен в .env файл:\n"
            "  TELEGRAM_TOKEN=твой_токен"
        )

    # Запускаем веб-сервер в отдельном потоке (для Railway/UptimeRobot)
    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))

    # Текстовые сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Картинки, видео, документы (обрабатываем подпись)
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_message))

    # Глобальный обработчик ошибок
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Исключение при обработке обновления: %s", context.error, exc_info=context.error)

    app.add_error_handler(error_handler)

    logger.info("🦉 Сова запускается...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,  # Пропускаем старые сообщения при перезапуске
    )


if __name__ == "__main__":
    main()
