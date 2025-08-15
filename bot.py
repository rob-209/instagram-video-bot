import os
import re
import requests
import tempfile
import instaloader
import logging
import time
import signal
import random
import json
from datetime import datetime
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
        # Создаем уникальную папку для сессии
        session_dir = os.path.join(tempfile.gettempdir(), f"instaloader_session_{int(time.time())}")
        os.makedirs(session_dir, exist_ok=True)
        
        self.L = instaloader.Instaloader(
            dirname_pattern=session_dir,
            download_pictures=False,
            download_videos=False,
            save_metadata=False,
            compress_json=False,
            sleep=True,
            quiet=True,
            request_timeout=120,
            user_agent=None  # Будем устанавливать динамически
        )
        
        # Динамические User-Agent
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15',
            'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1'
        ]
        
        # Установка случайного User-Agent
        self.set_random_user_agent()
        
        # Аутентификация в Instagram
        self.authenticate()
        
        self.request_delay = random.randint(5, 15)  # Случайная задержка
        self.max_retries = 3
        self.session_file = os.path.join(session_dir, "session.json")

    def set_random_user_agent(self):
        """Устанавливает случайный User-Agent"""
        user_agent = random.choice(self.user_agents)
        self.L.context._session.headers.update({
            'User-Agent': user_agent,
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'DNT': '1'
        })
        logger.info(f"🔄 Установлен User-Agent: {user_agent[:50]}...")

    def authenticate(self):
        """Аутентификация с повторными попытками"""
        if not INSTA_USERNAME or not INSTA_PASSWORD:
            logger.warning("⚠️ Учетные данные Instagram не предоставлены. Работаем без аутентификации")
            return
            
        for attempt in range(3):
            try:
                # Пробуем загрузить существующую сессию
                if os.path.exists(self.session_file):
                    with open(self.session_file, 'r') as f:
                        session_data = json.load(f)
                    
                    if time.time() - session_data['timestamp'] < 86400:  # 24 часа
                        self.L.context.load_session(INSTA_USERNAME, session_data['session'])
                        logger.info("✅ Сессия Instagram загружена из кэша")
                        return
                
                # Новая аутентификация
                self.L.login(INSTA_USERNAME, INSTA_PASSWORD)
                logger.info("✅ Успешная аутентификация в Instagram")
                
                # Сохраняем сессию
                session_data = {
                    'session': self.L.context.get_session().get_dict(),
                    'timestamp': time.time()
                }
                with open(self.session_file, 'w') as f:
                    json.dump(session_data, f)
                
                return
                
            except instaloader.exceptions.BadCredentialsException:
                logger.error("❌ Неверные учетные данные Instagram")
                return
            except instaloader.exceptions.ConnectionException as e:
                logger.warning(f"🔄 Ошибка подключения ({attempt+1}/3): {str(e)}")
                time.sleep(5)
            except Exception as e:
                logger.error(f"⚠️ Ошибка аутентификации: {str(e)}")
                time.sleep(10)
        
        logger.error("❌ Не удалось аутентифицироваться в Instagram после 3 попыток")

    async def download_media(self, url: str, update: Update):
        """Основная функция загрузки медиа"""
        try:
            # Извлекаем shortcode
            shortcode = self._extract_shortcode(url)
            if not shortcode:
                return "❌ Неверная ссылка Instagram. Поддерживаются только посты, Reels и IGTV."

            # Создаем временную папку
            with tempfile.TemporaryDirectory() as temp_dir:
                for attempt in range(1, self.max_retries + 1):
                    try:
                        # Обновляем User-Agent перед каждым запросом
                        self.set_random_user_agent()
                        
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
                            time.sleep(random.uniform(1, 3))  # Случайная задержка

                        return "✅ Скачивание завершено!" if all(results) else "⚠️ Возникли ошибки при скачивании некоторых медиа"
                    
                    except instaloader.exceptions.QueryReturnedBadRequestException as e:
                        if "401" in str(e):
                            logger.warning(f"🔄 Ошибка 401, попытка {attempt}/{self.max_retries}")
                            self.authenticate()  # Повторная аутентификация
                            time.sleep(5)
                        else:
                            raise
                    except requests.exceptions.RequestException as e:
                        logger.warning(f"🔄 Сетевая ошибка, попытка {attempt}/{self.max_retries}: {str(e)}")
                        time.sleep(10)

                return "❌ Не удалось обработать запрос после нескольких попыток"

        except instaloader.exceptions.InstaloaderException as e:
            logger.error(f"Instaloader error: {e}")
            return f"❌ Ошибка Instagram: {str(e)}"
        except Exception as e:
            logger.exception("Unexpected error")
            return f"⚠️ Неожиданная ошибка: {str(e)}"

    # ... (остальные методы без изменений: _extract_shortcode, _get_media_items, _process_media)

# Инициализация загрузчика
downloader = InstagramDownloader()

# ... (остальные функции без изменений: start, handle_message, post_init, main)
