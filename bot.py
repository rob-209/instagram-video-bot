import os
import re
import requests
import tempfile
import logging
import time
import random
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import instaloader

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загружаем переменные окружения
load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

if not TOKEN:
    raise ValueError("❌ Токен бота не найден! Создайте .env файл с TELEGRAM_BOT_TOKEN")

class InstagramDownloader:
    def __init__(self):
        self.L = instaloader.Instaloader(
            download_pictures=False,
            download_videos=False,
            save_metadata=False,
            compress_json=False,
            sleep=True,
            quiet=True
        )
        self.max_retries = 3
        self.request_delay = 2

    def _extract_shortcode(self, url: str) -> str:
        patterns = [
            r"instagram\.com/p/([^/?]+)",
            r"instagram\.com/reel/([^/?]+)",
            r"instagram\.com/tv/([^/?]+)",
            r"instagram\.com/stories/[^/]+/(\d+)"
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def _get_media_items(self, post) -> list:
        try:
            if post.typename == 'GraphSidecar':
                return [
                    ('video' if node.is_video else 'photo', 
                     node.video_url if node.is_video else node.display_url)
                    for node in post.get_sidecar_nodes()
                ]
            return [
                ('video' if post.is_video else 'photo',
                 post.video_url if post.is_video else post.url)
            ]
        except Exception:
            return []

    async def _process_media(self, media_type, media_url, temp_dir, update, counter):
        try:
            ext = '.mp4' if media_type == 'video' else '.jpg'
            file_path = os.path.join(temp_dir, f"media_{counter.replace('/', '_')}{ext}")
            retry_count = 0

            while retry_count < self.max_retries:
                try:
                    response = requests.get(media_url, stream=True, timeout=60)
                    response.raise_for_status()
                    break
                except requests.exceptions.RequestException as e:
                    retry_count += 1
                    if retry_count >= self.max_retries:
                        raise
                    logger.warning(f"🔄 Повторная попытка скачивания {retry_count}/{self.max_retries}")
                    time.sleep(1 + retry_count)

            file_size = int(response.headers.get('content-length', 0))
            max_size = 50 * 1024 * 1024

            if file_size > max_size:
                await update.message.reply_text(f"⚠️ Медиа {counter} слишком большое для Telegram ({file_size//1024//1024}MB)")
                return False

            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            if media_type == 'video':
                await update.message.reply_video(video=open(file_path, 'rb'), caption=f"🎬 Видео {counter}")
            else:
                await update.message.reply_photo(photo=open(file_path, 'rb'), caption=f"🖼 Фото {counter}")

            return True
        except Exception as e:
            logger.error(f"Media processing error: {e}")
            await update.message.reply_text(f"⚠️ Ошибка при обработке медиа {counter}: {str(e)}")
            return False

    async def download_media(self, url: str, update: Update):
        try:
            shortcode = self._extract_shortcode(url)
            if not shortcode:
                return "❌ Неверная ссылка Instagram."

            with tempfile.TemporaryDirectory() as temp_dir:
                try:
                    post = instaloader.Post.from_shortcode(self.L.context, shortcode)
                except Exception as e:
                    return f"❌ Ошибка получения поста: {e}"

                media_items = self._get_media_items(post)
                if not media_items:
                    return "❌ Медиа не найдено"

                results = []
                for idx, (media_type, media_url) in enumerate(media_items, 1):
                    result = await self._process_media(media_type, media_url, temp_dir, update, f"{idx}/{len(media_items)}")
                    results.append(result)
                    time.sleep(self.request_delay + random.uniform(0, 1))

                return "✅ Скачивание завершено!" if all(results) else "⚠️ Некоторые медиа не удалось скачать"
        except Exception as e:
            logger.exception("Unexpected error")
            return f"⚠️ Ошибка: {str(e)}"

# Инициализация загрузчика
downloader = InstagramDownloader()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📸 Instagram Media Downloader\n\n"
        "Отправь ссылку на пост, Reels или IGTV. Бот скачает видео и фото (если пост публичный)."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not re.match(r'https?://(www\.)?instagram\.com/(p|reel|tv|stories)/', url):
        await update.message.reply_text("❌ Отправьте корректную ссылку на Instagram.")
        return
    msg = await update.message.reply_text("⏳ Загружаю...")
    result = await downloader.download_media(url, update)
    await msg.edit_text(result)

async def post_init(application: Application):
    bot_info = await application.bot.get_me()
    logger.info(f"✅ Бот @{bot_info.username} запущен!")

def main():
    logger.info("Запуск Telegram-бота...")
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
