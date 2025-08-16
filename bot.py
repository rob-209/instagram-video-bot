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
            request_timeout=120
        )

        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15',
            'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1'
        ]

        self.L.context._session.headers.update({
            'User-Agent': random.choice(user_agents),
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'DNT': '1'
        })

        if INSTA_USERNAME and INSTA_PASSWORD:
            try:
                self.L.login(INSTA_USERNAME, INSTA_PASSWORD)
                logger.info("✅ Успешная аутентификация в Instagram")
            except Exception as e:
                logger.error(f"❌ Ошибка аутентификации в Instagram: {e}")
        else:
            logger.warning("⚠️ Учетные данные Instagram не предоставлены. Работаем без аутентификации")

        self.request_delay = 5
        self.max_retries = 3

    async def download_media(self, url: str, update: Update):
        try:
            shortcode = self._extract_shortcode(url)
            if not shortcode:
                return "❌ Неверная ссылка Instagram. Поддерживаются только посты, Reels и IGTV."

            with tempfile.TemporaryDirectory() as temp_dir:
                retry_count = 0
                post = None

                while retry_count < self.max_retries:
                    try:
                        post = instaloader.Post.from_shortcode(self.L.context, shortcode)

                        if post.owner_profile.is_private and not post.owner_profile.followed_by_viewer:
                            profile_url = f"https://instagram.com/{post.owner_username}"
                            return f"⚠️ Не могу скачать: аккаунт приватный.\nПрофиль: {profile_url}"

                        break
                    except instaloader.exceptions.QueryReturnedBadRequestException as e:
                        retry_count += 1
                        if retry_count >= self.max_retries:
                            raise
                        logger.warning(f"🔄 Повторная попытка {retry_count}/{self.max_retries} после ошибки: {e}")
                        time.sleep(2 ** retry_count)

                if not post:
                    return "❌ Не удалось получить пост."

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
                    time.sleep(self.request_delay + random.uniform(0, 1))

                return "✅ Скачивание завершено!" if all(results) else "⚠️ Некоторые медиа не удалось скачать"

        except instaloader.exceptions.InstaloaderException as e:
            logger.error(f"Instaloader error: {e}")
            return f"❌ Ошибка Instagram: {str(e)}"

        except Exception as e:
            logger.exception("Unexpected error")
            return f"⚠️ Неожиданная ошибка: {str(e)}"

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
                await update.message.reply_text(
                    f"⚠️ Медиа {counter} слишком большое для скачивания ({file_size//1024//1024}MB)"
                )
                return False

            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

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


