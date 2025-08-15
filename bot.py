import os
import re
import requests
import tempfile
import instaloader
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

    async def download_media(self, url: str, update: Update):
        """Основная функция загрузки медиа"""
        try:
            # Извлекаем shortcode
            shortcode = self._extract_shortcode(url)
            if not shortcode:
                return "❌ Неверная ссылка Instagram"

            # Создаем временную папку
            with tempfile.TemporaryDirectory() as temp_dir:
                post = instaloader.Post.from_shortcode(self.L.context, shortcode)
                
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

                return "✅ Скачивание завершено!" if all(results) else "⚠️ Возникли ошибки при скачивании"

        except instaloader.exceptions.InstaloaderException as e:
            return f"❌ Ошибка Instaloader: {str(e)}"
        except Exception as e:
            return f"⚠️ Ошибка: {str(e)}"

    def _extract_shortcode(self, url: str) -> str:
        """Извлекает shortcode из URL"""
        patterns = [
            r"instagram\.com/p/([^/?]+)",
            r"instagram\.com/reel/([^/?]+)", 
            r"instagram\.com/tv/([^/?]+)"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def _get_media_items(self, post) -> list:
        """Получает список медиа из поста"""
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

    async def _process_media(self, media_type, media_url, temp_dir, update, counter):
        """Обрабатывает и отправляет медиа"""
        try:
            ext = '.mp4' if media_type == 'video' else '.jpg'
            file_path = os.path.join(temp_dir, f"media_{counter.replace('/', '_')}{ext}")
            
            # Скачиваем файл
            response = requests.get(media_url, stream=True, timeout=60)
            response.raise_for_status()
            
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            # Отправляем в Telegram
            if media_type == 'video':
                await update.message.reply_video(
                    video=open(file_path, 'rb'),
                    supports_streaming=True,
                    caption=f"Медиа {counter}"
                )
            else:
                await update.message.reply_photo(
                    photo=open(file_path, 'rb'),
                    caption=f"Медиа {counter}"
                )
                
            return True
        except Exception as e:
            await update.message.reply_text(f"⚠️ Ошибка при обработке медиа {counter}: {str(e)}")
            return False

# Инициализация загрузчика
downloader = InstagramDownloader()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start"""
    await update.message.reply_text(
        "📸 Instagram Downloader\n\n"
        "Отправьте мне ссылку на:\n"
        "• Пост (фото/видео/карусель)\n"
        "• Reels\n"
        "• IGTV\n\n"
        "Пример: https://www.instagram.com/p/Cxyz...\n"
        "Бот скачает все медиа из поста.\n\n"
        "Важно: Работает только с публичными аккаунтами!"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка сообщений с ссылками"""
    url = update.message.text.strip()
    
    if not re.match(r'https?://(www\.)?instagram\.com/(p|reel|tv)/', url):
        await update.message.reply_text("🔗 Отправьте корректную ссылку Instagram")
        return
    
    msg = await update.message.reply_text("⏳ Обрабатываю ссылку...")
    result = await downloader.download_media(url, update)
    await msg.edit_text(result)

async def post_init(application: Application):
    """Функция инициализации после запуска"""
    bot_info = await application.bot.get_me()
    print(f"✅ Бот @{bot_info.username} запущен!")

def main():
    """Запуск бота"""
    print("🔄 Запускаю бота...")
    
    try:
        app = Application.builder().token(TOKEN).post_init(post_init).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.run_polling()
    except Exception as e:
        print(f"❌ Ошибка запуска: {e}")

if __name__ == "__main__":
    main()