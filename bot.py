import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from yt_dlp import YoutubeDL
from dotenv import load_dotenv

# Загрузка токена
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Логгирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# YT-DLP настройки
ydl_opts = {
    'format': 'best[ext=mp4]/best',
    'outtmpl': 'downloaded_video.%(ext)s',
    'quiet': True,
    'noplaylist': True,
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📥 Отправь ссылку на Instagram Reel или пост с видео")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not url.startswith("https://www.instagram.com/"):
        await update.message.reply_text("❌ Отправь корректную ссылку на Instagram")
        return

    msg = await update.message.reply_text("⏳ Скачиваю видео...")

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)

        await update.message.reply_video(
            video=open(file_path, "rb"),
            caption="🎬 Вот твоё видео"
        )
        os.remove(file_path)

        await msg.delete()

    except Exception as e:
        logger.error(f"Ошибка загрузки: {e}")
        await msg.edit_text("❌ Не удалось скачать видео. Возможно, ссылка недействительна или видео приватное.")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
