import os
import re
import requests
import tempfile
import instaloader
import logging
import time
import signal
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

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
            dirname_pattern=tempfile.gettempdir(),
            download_pictures=False,
            download_videos=False,
            save_metadata=False,
            compress_json=False,
            sleep=True,  # Добавляем задержки между запросами
            quiet=True
        )
        
        # Настройка пользовательского агента
        self.L.context._session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        
        self.request_delay = 3  # Задержка между запросами в секундах

    async def download_media(self, url: str, update: Update):
        """Основная функция загрузки медиа"""
        try:
            # Извлекаем shortcode
            shortcode = self._extract_shortcode(url)
            if not shortcode:
                return "❌ Неверная ссылка Instagram. Поддерживаются только посты, Reels и IGTV."

            # Создаем временную папку
            with tempfile.TemporaryDirectory() as temp_dir:
                post = instaloader.Post.from_shortcode(self.L.context, shortcode)
                
                # Проверяем приватный аккаунт
                if post.owner_profile.is_private:
                    return "❌ Не могу скачать: аккаунт приватный. Бот работает только с публичными профилями."
                
                media_items = self._get_media_items(post)
                if not media_items:
                    return "❌ Медиа не найдено"

                results = []
                for idx, (media_type, media_url) in enumerate(media_items, 1):
                    result = await self._process_media(
                        media_type, media_url, temp_dir, update, 
                        f"{idx}/{len(media_items)}"
                    )
                    results.append(result)
                    time.sleep(self.request_delay)  # Задержка между медиа

                return "✅ Скачивание завершено!" if all(results) else "⚠️ Возникли ошибки при скачивании некоторых медиа"

        except instaloader.exceptions.InstaloaderException as e:
            logger.error(f"Instaloader error: {e}")
            return f"❌ Ошибка Instagram: {str(e)}"
        except Exception as e:
            logger.exception("Unexpected error")
            return f"⚠️ Неожиданная ошибка: {str(e)}"

    def _extract_shortcode(self, url: str) -> str:
        """Извлекает shortcode из URL"""
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
        """Получает список медиа из поста"""
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
        """Обрабатывает и отправляет медиа"""
        try:
            ext = '.mp4' if media_type == 'video' else '.jpg'
            file_path = os.path.join(temp_dir, f"media_{counter.replace('/', '_')}{ext}")
            
            # Скачиваем файл
            response = requests.get(media_url, stream=True, timeout=60)
            response.raise_for_status()
            
            # Проверяем размер файла (макс. 50MB)
            file_size = int(response.headers.get('content-length', 0))
            max_size = 50 * 1024 * 1024  # 50MB
            
            if file_size > max_size:
                await update.message.reply_text(
                    f"⚠️ Медиа {counter} слишком большое для скачивания ({file_size//1024//1024}MB)"
                )
                return False
            
            # Скачиваем файл
            with open(file_path, 'wb') as f:
                downloaded = 0
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
            
            # Отправляем в Telegram
            if media_type == 'video':
                await update.message.reply_video(
                    video=open(file_path, 'rb'),
                    supports_streaming=True,
                    caption=f"Видео {counter}",
                    read_timeout=60,
                    write_timeout=60
                )
            else:
                await update.message.reply_photo(
                    photo=open(file_path, 'rb'),
                    caption=f"Фото {counter}",
                    read_timeout=60,
                    write_timeout=60
                )
                
            return True
        except Exception as e:
            logger.error(f"Media processing error: {e}")
            await update.message.reply_text(f"⚠️ Ошибка при обработке медиа {counter}: {str(e)}")
            return False

# Инициализация загрузчика
downloader = InstagramDownloader()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start"""
    await update.message.reply_text(
        "📸 Instagram Media Downloader\n\n"
        "Отправьте мне ссылку на:\n"
        "• Пост (фото/видео/карусель)\n"
        "• Reels\n"
        "• IGTV\n\n"
        "Примеры:\n"
        "https://www.instagram.com/p/Cxyz...\n"
        "https://www.instagram.com/reel/Cabc...\n\n"
        "⚠️ Важно:\n"
        "- Работает только с публичными аккаунтами\n"
        "- Макс. размер файла: 50MB"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка сообщений с ссылками"""
    url = update.message.text.strip()
    
    if not re.match(r'https?://(www\.)?instagram\.com/(p|reel|tv|stories)/', url):
        await update.message.reply_text(
            "🔗 Отправьте корректную ссылку Instagram.\n"
            "Поддерживаемые форматы:\n"
            "• Посты: https://www.instagram.com/p/...\n"
            "• Reels: https://www.instagram.com/reel/...\n"
            "• IGTV: https://www.instagram.com/tv/...\n"
            "• Stories: https://www.instagram.com/stories/..."
        )
        return
    
    msg = await update.message.reply_text("⏳ Обрабатываю ссылку...")
    result = await downloader.download_media(url, update)
    await msg.edit_text(result)

async def post_init(application: Application):
    """Функция инициализации после запуска"""
    bot_info = await application.bot.get_me()
    logger.info(f"✅ Бот @{bot_info.username} запущен!")
    print(f"✅ Бот @{bot_info.username} запущен!")

def main():
    """Запуск бота"""
    print("🔄 Запускаю бота...")
    logger.info("Starting bot...")
    
    try:
        app = Application.builder().token(TOKEN).post_init(post_init).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Обработка сигналов для корректного завершения
        app.run_polling(stop_signals=[signal.SIGINT, signal.SIGTERM])
        
    except Exception as e:
        logger.critical(f"❌ Ошибка запуска: {e}")
        print(f"❌ Критическая ошибка: {e}")

if __name__ == "__main__":
    main()
