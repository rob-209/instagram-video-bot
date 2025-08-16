import os
import re
import requests
import tempfile
import instaloader
import logging
import time
import signal
import random
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
INSTA_USERNAME = os.getenv('INSTAGRAM_USERNAME')
INSTA_PASSWORD = os.getenv('INSTAGRAM_PASSWORD')

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
            sleep=True,
            quiet=True,
            request_timeout=120  # Увеличиваем таймаут запросов
        )
        
        # Случайный User-Agent для каждого экземпляра
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15',
            'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1'
        ]
        
        # Настройка пользовательского агента
        self.L.context._session.headers.update({
            'User-Agent': random.choice(user_agents),
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'DNT': '1'
        })
        
        # Аутентификация в Instagram
        if INSTA_USERNAME and INSTA_PASSWORD:
            try:
                self.L.login(INSTA_USERNAME, INSTA_PASSWORD)
                logger.info("✅ Успешная аутентификация в Instagram")
            except Exception as e:
                logger.error(f"❌ Ошибка аутентификации в Instagram: {e}")
        else:
            logger.warning("⚠️ Учетные данные Instagram не предоставлены. Работаем без аутентификации")
        
        self.request_delay = 5  # Увеличили задержку между запросами
        self.max_retries = 3    # Максимальное количество попыток

        async def download_media(self, url: str, update: Update):
        """Основная функция загрузки медиа"""
        try:
            # Извлекаем shortcode
            shortcode = self._extract_shortcode(url)
            if not shortcode:
                return "❌ Неверная ссылка Instagram. Поддерживаются только посты, Reels и IGTV."

            # Создаем временную папку
            with tempfile.TemporaryDirectory() as temp_dir:
                retry_count = 0
                post = None

                while retry_count < self.max_retries:
                    try:
                        post = instaloader.Post.from_shortcode(self.L.context, shortcode)

                        # Проверка: если профиль приватный и не авторизованы
                        if post.owner_profile.is_private and not post.owner_profile.followed_by_viewer:
                            profile_url = f"https://instagram.com/{post.owner_username}"
                            return f"⚠️ Не могу скачать: аккаунт приватный.\nПрофиль: {profile_url}"

                        break  # Всё ок, пост получен

                    except instaloader.exceptions.QueryReturnedBadRequestException as e:
                        retry_count += 1
                        if retry_count >= self.max_retries:
                            raise
                        logger.warning(f"🔄 Повторная попытка {retry_count}/{self.max_retries} после ошибки: {e}")
                        time.sleep(2 ** retry_count)

                # Проверяем, что пост получен
                if not post:
                    return "❌ Не удалось получить пост."

                # Получаем медиа
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
                    time.sleep(self.request_delay + random.uniform(0, 1))  # случайная задержка

                return "✅ Скачивание завершено!" if all(results) else "⚠️ Некоторые медиа не удалось скачать"

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
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:  # фильтруем keep-alive пакеты
                        f.write(chunk)
            
            # Отправляем в Telegram
            if media_type == 'video':
                await update.message.reply_video(
                    video=open(file_path, 'rb'),
                    supports_streaming=True,
                    caption=f"Видео {counter}",
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=120
                )
            else:
                await update.message.reply_photo(
                    photo=open(file_path, 'rb'),
                    caption=f"Фото {counter}",
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=120
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

