import asyncio
import logging
import threading
from typing import Optional, TYPE_CHECKING, Dict
from aiogram import Bot, Dispatcher
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from ..utils.database import Database

if TYPE_CHECKING:
    from ..core.app_manager import AppManager

logger = logging.getLogger(__name__)

class ParsingStates(StatesGroup):
    waiting_for_url = State()
    waiting_for_count = State()
    settings_menu = State()
    waiting_for_default_count = State()

FIELD_NAMES = {
    'article': 'Артикул',
    'name': 'Название товара',
    'seller_name': 'Продавец',
    'company_name': 'Название компании',
    'inn': 'ИНН',
    'card_price': 'Цена карты',
    'price': 'Цена',
    'original_price': 'Старая цена',
    'product_url': 'Ссылка на товар',
    'image_url': 'Ссылка на изображение',
    'orders_count': 'Количество заказов',
    'reviews_count': 'Количество отзывов',
    'average_rating': 'Рейтинг',
    'working_time': 'Работает с'
}

class TelegramBotManager:
    
    def __init__(self, bot_token: str, user_ids: list, app_manager: 'AppManager'):
        self.bot_token = bot_token
        self.user_ids = user_ids  # Список разрешенных User ID
        self.app_manager = app_manager
        self.bot = Bot(token=bot_token)
        self.dp = Dispatcher()
        self.is_running = False
        self.bot_thread: Optional[threading.Thread] = None
        self.db = Database()
        self.user_data: Dict[str, dict] = {}
        self.parsing_user_id = None
        
        self._register_handlers()

    
    def start(self) -> bool:
        try:
            # Запускаем бот в отдельном потоке
            self.bot_thread = threading.Thread(target=self._run_bot, daemon=True)
            self.bot_thread.start()
            
            # Даем боту время на инициализацию
            import time
            time.sleep(2)
            
            if self.is_running:
                # Создаем отдельный поток для отправки стартового сообщения
                notification_thread = threading.Thread(
                    target=self._send_startup_notification,
                    daemon=True
                )
                notification_thread.start()
                return True
            else:
                return False
                
        except Exception as e:
            logger.error(f"Ошибка запуска Telegram бота: {e}")
            return False

    def _send_startup_notification(self):
        """Отправляет уведомление о запуске в отдельном потоке"""
        try:
            # Создаем новый бот для отправки сообщения
            # Это избегает проблем с контекстом asyncio
            temp_bot = Bot(token=self.bot_token)
            
            async def send_and_close():
                try:
                    # Отправляем уведомление всем разрешенным пользователям
                    for user_id in self.user_ids:
                        await temp_bot.send_message(chat_id=user_id, 
                                                text="🤖 Ozon Parser бот запущен и готов к работе!")
                finally:
                    await temp_bot.session.close()
            
            # Запускаем в новом цикле событий
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(send_and_close())
            finally:
                loop.close()
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о запуске: {e}")
    
    def _run_bot(self):
        loop = None
        try:
            # Настройка политики для Windows
            import sys
            if sys.platform == 'win32':
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

            # Создаем и устанавливаем новый event loop для этого потока
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            self.is_running = True
            
            # Запускаем polling в этом loop с отключенной обработкой сигналов
            # handle_signals=False критически важен для запуска в отдельном потоке
            loop.run_until_complete(self.dp.start_polling(self.bot, handle_signals=False))
            
        except Exception as e:
            logger.error(f"Ошибка работы Telegram бота: {e}")
            self.is_running = False
        finally:
            # Закрываем loop при выходе
            if loop and not loop.is_closed():
                try:
                    loop.close()
                except Exception as e:
                    logger.error(f"Ошибка закрытия event loop: {e}")
    
    def _register_handlers(self):
        self.dp.message.register(self._cmd_start, Command('start'))
        self.dp.message.register(self._cmd_status, Command('status'))
        self.dp.message.register(self._cmd_settings, Command('settings'))
        self.dp.message.register(self._cmd_help, Command('help'))
        
        self.dp.callback_query.register(self._handle_callback)
        self.dp.message.register(self._handle_url_input, StateFilter(ParsingStates.waiting_for_url))
        self.dp.message.register(self._handle_count_input, StateFilter(ParsingStates.waiting_for_count))
        self.dp.message.register(self._handle_default_count_input, StateFilter(ParsingStates.waiting_for_default_count))
        self.dp.message.register(self._handle_message)
    
    async def _cmd_start(self, message: Message, state: FSMContext = None):
        await self._show_main_menu(message, state)

    async def _show_main_menu(self, message_or_query, state: FSMContext = None):
        if not self._is_authorized_user(message_or_query):
            return

        if state:
            await state.clear()

        keyboard = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="🚀 Начать парсинг"), KeyboardButton(text="📊 Статус")],
            [KeyboardButton(text="🔧 Ресурсы"), KeyboardButton(text="⚙️ Настройки")],
            [KeyboardButton(text="❓ Помощь")]
        ], resize_keyboard=True)
        
        welcome_text = (
            "🤖 <b>Добро пожаловать в Ozon Parser!</b>\n\n"
            "Выберите действие из меню ниже:"
        )
        
        await self._reply_to(message_or_query, welcome_text, reply_markup=keyboard, parse_mode="HTML")
    
    async def _cmd_status(self, message: Message):
        await self._show_status(message)
    
    async def _show_status(self, message_or_query):
        if not self._is_authorized_user(message_or_query):
            return
        
        status = self.app_manager.get_status()
        
        status_text = f"📊 <b>Статус парсера</b>\n\n"
        status_text += f"🔄 Парсинг: {'🟢 Активен' if status['is_running'] else '🔴 Остановлен'}\n"
        status_text += f"👥 Активных пользователей: {status.get('active_users_count', 0)}\n"
        status_text += f"🤖 Telegram бот: 🟢 Активен\n"
        status_text += f"📦 Макс. товаров: {status['settings']['max_products']}\n"
        status_text += f"⚙️ Макс. воркеров: {status['settings']['max_workers']}\n"
        
        # Показываем информацию о ресурсах
        if status.get('total_active_users', 0) > 0:
            status_text += f"\n🔧 <b>Ресурсы:</b>\n"
            status_text += f"⚙️ Используется воркеров: {status.get('total_allocated_workers', 0)}/10\n"
        
        # Показываем результаты для текущего пользователя
        user_id = str(message_or_query.from_user.id)
        user_results = self.app_manager.get_user_results(user_id)
        
        if user_results:
            status_text += f"\n📈 <b>Ваши результаты:</b>\n"
            status_text += f"✅ Успешно: {user_results.get('successful_products', 0)}/{user_results.get('total_products', 0)}"
        elif status['last_results']:
            # Fallback для совместимости
            results = status['last_results']
            status_text += f"\n📈 <b>Последний результат:</b>\n"
            status_text += f"✅ Успешно: {results.get('successful_products', 0)}/{results.get('total_products', 0)}"
        
        keyboard = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="🔄 Обновить"), KeyboardButton(text="🏠 Главное меню")]
        ], resize_keyboard=True)
        
        await self._reply_to(message_or_query, status_text, reply_markup=keyboard, parse_mode="HTML")
    
    async def _show_resources_status(self, message_or_query):
        if not self._is_authorized_user(message_or_query):
            return
        
        try:
            from ..utils.resource_manager import resource_manager
            status = resource_manager.get_status()
            
            status_text = "🔧 <b>Статус ресурсов</b>\n\n"
            
            if status['total_active_users'] == 0:
                status_text += "😴 Нет активных пользователей\n"
                status_text += f"📊 Доступно воркеров: {resource_manager.MAX_TOTAL_WORKERS}\n"
            else:
                status_text += f"👥 Активных пользователей: {status['total_active_users']}\n"
                status_text += f"⚙️ Используется воркеров: {status['total_allocated_workers']}/{resource_manager.MAX_TOTAL_WORKERS}\n\n"
                
                for user_id, session_info in status['sessions'].items():
                    user_display = f"User_{user_id[-4:]}" if len(user_id) > 4 else user_id
                    status_text += f"👤 <b>{user_display}</b>\n"
                    status_text += f"   📋 Этап: {session_info['stage']}\n"
                    status_text += f"   ⚙️ Воркеров: {session_info['workers']}\n"
                    status_text += f"   📈 Прогресс: {session_info['progress']}\n"
                    status_text += f"   ⏱ Время: {session_info['duration']}\n\n"
            
            status_text += f"\n📋 <b>Лимиты:</b>\n"
            status_text += f"• Макс воркеров всего: {resource_manager.MAX_TOTAL_WORKERS}\n"
            status_text += f"• Макс на пользователя: {resource_manager.MAX_WORKERS_PER_USER}\n"
            status_text += f"• Мин на пользователя: {resource_manager.MIN_WORKERS_PER_USER}\n"
            
        except Exception as e:
            status_text = f"❌ Ошибка получения статуса ресурсов: {e}"
        
        await self._reply_to(message_or_query, status_text, parse_mode="HTML")
    
    async def _cmd_settings(self, message: Message, state: FSMContext):
        await self._show_settings(message, state)
    
    async def _show_settings(self, message_or_query, state: FSMContext):
        if not self._is_authorized_user(message_or_query):
            return
        
        user_id = str(message_or_query.from_user.id)
        settings = self.db.get_user_settings(user_id)
        default_count = settings.get('default_product_count', 500)
        
        text = "⚙️ <b>Настройки</b>\n\n"
        text += f"📊 Количество товаров по умолчанию: {default_count}\n\n"
        
        keyboard = [
            [InlineKeyboardButton(text="🔢 Изменить количество товаров", callback_data="change_default_count")],
            [InlineKeyboardButton(text="📝 Настроить поля экспорта", callback_data="configure_fields")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ]
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await state.set_state(ParsingStates.settings_menu)
        
        if isinstance(message_or_query, CallbackQuery):
            await message_or_query.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await message_or_query.reply(text, reply_markup=reply_markup, parse_mode="HTML")
    
    async def _cmd_help(self, message: Message):
        await self._show_help(message)
    
    async def _show_help(self, message_or_query):
        if not self._is_authorized_user(message_or_query):
            return
        
        help_text = (
            "🆘 <b>Помощь по использованию</b>\n\n"
            "<b>Как начать парсинг:</b>\n"
            "1️⃣ Нажмите '🚀 Начать парсинг'\n"
            "2️⃣ Отправьте ссылку на категорию Ozon\n"
            "3️⃣ Укажите количество товаров\n"
            "4️⃣ Дождитесь завершения\n\n"
            "<b>Пример ссылки:</b>\n"
            "<code>https://ozon.ru/category/sistemnye-bloki-15704/</code>\n\n"
            "<b>Настройки:</b>\n"
            "В настройках можно выбрать какие поля экспортировать в Excel файл.\n\n"
            "Бот будет уведомлять вас о ходе парсинга 📊"
        )
        
        keyboard = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="🏠 Главное меню")]
        ], resize_keyboard=True)
        
        await self._reply_to(message_or_query, help_text, reply_markup=keyboard, parse_mode="HTML")
    
    async def _handle_callback(self, query: CallbackQuery, state: FSMContext):
        if not self._is_authorized_user(query):
            return
        
        data = query.data
        
        if data == "main_menu":
            await state.clear()
            await self._show_main_menu(query, state)
        elif data == "start_parsing":
            await self._start_parsing_flow(query, state)
        elif data == "status":
            await self._show_status(query)
        elif data == "resources":
            await self._show_resources_status(query)
        elif data == "settings":
            await self._show_settings(query, state)
        elif data == "help":
            await self._show_help(query)
        elif data == "cancel_parsing":
            await state.clear()
            keyboard = ReplyKeyboardMarkup(keyboard=[
                [KeyboardButton(text="🏠 Главное меню")]
            ], resize_keyboard=True)
            await query.message.edit_text("❌ Парсинг отменен")
            await query.message.reply("Выберите действие:", reply_markup=keyboard)
        elif data == "stop_parsing":
            self.app_manager.stop_parsing()
            keyboard = ReplyKeyboardMarkup(keyboard=[
                [KeyboardButton(text="🏠 Главное меню")]
            ], resize_keyboard=True)
            await query.message.edit_text("⏹️ Парсинг остановлен")
            await query.message.reply("Выберите действие:", reply_markup=keyboard)
        elif data == "skip_count":
            user_id = str(query.from_user.id)
            if user_id in self.user_data and 'url' in self.user_data[user_id]:
                settings = self.db.get_user_settings(user_id)
                default_count = settings.get('default_product_count', 500)
                await self._start_parsing_with_count(query, self.user_data[user_id]['url'], default_count)
            else:
                await query.answer("Сначала отправьте ссылку на категорию Ozon", show_alert=True)
                return
        elif data.startswith("toggle_field_"):
            await self._toggle_field(query, data.replace("toggle_field_", ""), state)
        elif data == "save_settings":
            await self._save_settings(query, state)
        elif data == "change_default_count":
            await self._change_default_count(query, state)
        elif data == "configure_fields":
            await self._configure_fields(query, state)
        elif data == "settings":
            await self._show_settings(query, state)
        else:
            await query.answer("Команда устарела. Откройте главное меню заново.", show_alert=True)
            await self._show_main_menu(query, state)
            return
        
        await query.answer()
    
    async def _start_parsing_flow(self, query: CallbackQuery, state: FSMContext):
        if self.app_manager.is_running:
            keyboard = ReplyKeyboardMarkup(keyboard=[
                [KeyboardButton(text="❌ Завершить"), KeyboardButton(text="🏠 Главное меню")]
            ], resize_keyboard=True)
            await query.message.edit_text("⚠️ Парсинг уже запущен. Дождитесь завершения или остановите его.")
            await query.message.reply("Выберите действие:", reply_markup=keyboard)
        else:
            keyboard = ReplyKeyboardMarkup(keyboard=[
                [KeyboardButton(text="❌ Отмена")]
            ], resize_keyboard=True)
            await query.message.edit_text("🔗 Отправьте ссылку на категорию Ozon:")
            await query.message.reply("Или нажмите кнопку:", reply_markup=keyboard)
            await state.set_state(ParsingStates.waiting_for_url)
    
    async def _handle_url_input(self, message: Message, state: FSMContext):
        if not self._is_authorized_user(message):
            return
        
        if message.text == "❌ Отмена":
            await state.clear()
            await self._cmd_start(message)
            return
        
        if not message.text or not self._is_ozon_category_url(message.text):
            keyboard = ReplyKeyboardMarkup(keyboard=[
                [KeyboardButton(text="❌ Отмена")]
            ], resize_keyboard=True)
            await message.reply("❌ Неверная ссылка. Отправьте ссылку на категорию Ozon:", reply_markup=keyboard)
            return
        
        user_id = str(message.from_user.id)
        if user_id not in self.user_data:
            self.user_data[user_id] = {}
        self.user_data[user_id]['url'] = message.text.strip()
        
        settings = self.db.get_user_settings(user_id)
        default_count = settings.get('default_product_count', 500)
        
        keyboard = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text=f"⏭️ Скип (по умолчанию {default_count})")],
            [KeyboardButton(text="❌ Отмена")]
        ], resize_keyboard=True)
        
        await message.reply("🔢 Введите количество товаров для парсинга (цифрами):", reply_markup=keyboard)
        await state.set_state(ParsingStates.waiting_for_count)
    
    async def _handle_count_input(self, message: Message, state: FSMContext):
        if not self._is_authorized_user(message):
            return
        
        if message.text == "❌ Отмена":
            await state.clear()
            await self._cmd_start(message)
            return
        
        user_id = str(message.from_user.id)
        settings = self.db.get_user_settings(user_id)
        default_count = settings.get('default_product_count', 500)
        
        if message.text == f"⏭️ Скип (по умолчанию {default_count})":
            url = self.user_data.get(user_id, {}).get('url')
            if url:
                await self._start_parsing_with_count(message, url, default_count)
            return
        
        try:
            count = int(message.text.strip())
            if count < 1 or count > 10000:
                raise ValueError()
        except ValueError:
            keyboard = ReplyKeyboardMarkup(keyboard=[
                [KeyboardButton(text=f"⏭️ Скип (по умолчанию {default_count})")],
                [KeyboardButton(text="❌ Отмена")]
            ], resize_keyboard=True)
            await message.reply("❌ Введите число от 1 до 10000:", reply_markup=keyboard)
            return
        
        user_id = str(message.from_user.id)
        url = self.user_data.get(user_id, {}).get('url')
        if url:
            await self._start_parsing_with_count(message, url, count)
        else:
            await message.reply("❌ Ошибка: URL не найден")
            await state.clear()
    
    async def _start_parsing_with_count(self, message_or_query, url: str, count: int):
        self.app_manager.settings.MAX_PRODUCTS = count
        user_id = str(message_or_query.from_user.id)
        
        keyboard = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="❌ Завершить")]
        ], resize_keyboard=True)
        
        text = f"🚀 Запускаю парсинг {count} товаров...\n\nЭто может занять несколько минут."
        
        await self._reply_to(message_or_query, text, reply_markup=keyboard)
        
        self.parsing_user_id = user_id
        
        # Получаем выбранные поля пользователя
        user_settings = self.db.get_user_settings(user_id)
        selected_fields = user_settings.get('selected_fields', [])
        
        def start_parsing():
            success = self.app_manager.start_parsing(url, selected_fields, user_id)
            if not success:
                self.send_message_sync("❌ Не удалось запустить парсинг. Возможно, он уже запущен.", user_id)
        
        threading.Thread(target=start_parsing, daemon=True).start()
    
    async def _toggle_field(self, query: CallbackQuery, field_key: str, state: FSMContext):
        user_id = str(query.from_user.id)
        settings = self.db.get_user_settings(user_id)
        selected_fields = settings['selected_fields']
        field_order = settings['field_order']
        default_count = settings.get('default_product_count', 500)
        
        if field_key in selected_fields:
            if len(selected_fields) > 2:
                selected_fields.remove(field_key)
                if field_key in field_order:
                    field_order.remove(field_key)
        else:
            selected_fields.append(field_key)
            field_order.append(field_key)
        
        self.db.save_user_settings(user_id, selected_fields, field_order, default_count)
        await self._configure_fields(query, state)
    
    async def _save_settings(self, query: CallbackQuery, state: FSMContext):
        keyboard = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="🏠 Главное меню")]
        ], resize_keyboard=True)
        await query.message.edit_text("✅ Настройки сохранены!")
        await query.message.reply("Выберите действие:", reply_markup=keyboard)
        await state.clear()
    
    async def _handle_message(self, message: Message):
        if not self._is_authorized_user(message):
            return
        
        text = message.text or ""
        
        if text == "🚀 Начать парсинг":
            await self._start_parsing_flow_from_keyboard(message)
        elif text == "📊 Статус":
            await self._show_status(message)
        elif text == "🔧 Ресурсы":
            await self._show_resources_status(message)
        elif text == "⚙️ Настройки":
            state = FSMContext(storage=self.dp.storage, key=f"user:{message.from_user.id}")
            await self._show_settings(message, state)
        elif text == "❓ Помощь":
            await self._show_help(message)
        elif text == "🏠 Главное меню":
            await self._cmd_start(message)
        elif text == "🔄 Обновить":
            await self._show_status(message)
        elif text == "❌ Завершить":
            self.app_manager.stop_parsing()
            await message.reply("⏹️ Парсинг остановлен")
            await self._cmd_start(message)
        elif self._is_ozon_category_url(text):
            # Обработка URL без состояния
            user_id = str(message.from_user.id)
            if user_id not in self.user_data:
                self.user_data[user_id] = {}
            self.user_data[user_id]['url'] = text.strip()
            self.user_data[user_id]['waiting_for_count'] = True
            
            user_id = str(message.from_user.id)
            settings = self.db.get_user_settings(user_id)
            default_count = settings.get('default_product_count', 500)
            
            keyboard = ReplyKeyboardMarkup(keyboard=[
                [KeyboardButton(text=f"⏭️ Скип (по умолчанию {default_count})")],
                [KeyboardButton(text="❌ Отмена")]
            ], resize_keyboard=True)
            
            await message.reply("🔢 Введите количество товаров для парсинга (цифрами):", reply_markup=keyboard)
        elif text and text.isdigit():
            # Обработка числового ввода
            user_id = str(message.from_user.id)
            if user_id in self.user_data and self.user_data[user_id].get('waiting_for_count'):
                count = int(text)
                if 1 <= count <= 10000:
                    url = self.user_data[user_id].get('url')
                    if url:
                        self.user_data[user_id]['waiting_for_count'] = False
                        await self._start_parsing_with_count(message, url, count)
                        return
                else:
                    keyboard = ReplyKeyboardMarkup(keyboard=[
                        [KeyboardButton(text="⏭️ Скип (по умолчанию 500)")],
                        [KeyboardButton(text="❌ Отмена")]
                    ], resize_keyboard=True)
                    await message.reply("❌ Введите число от 1 до 10000:", reply_markup=keyboard)
                    return
            
            await message.reply("❓ Используйте кнопки меню или команды:\n/start - главное меню\n/help - помощь")
        elif text.startswith("⏭️ Скип (по умолчанию"):
            user_id = str(message.from_user.id)
            if user_id in self.user_data and self.user_data[user_id].get('waiting_for_count'):
                url = self.user_data[user_id].get('url')
                if url:
                    self.user_data[user_id]['waiting_for_count'] = False
                    settings = self.db.get_user_settings(user_id)
                    default_count = settings.get('default_product_count', 500)
                    await self._start_parsing_with_count(message, url, default_count)
                    return
            await message.reply("❌ Сначала отправьте ссылку на категорию Ozon.")
        elif text == "❌ Отмена":
            user_id = str(message.from_user.id)
            if user_id in self.user_data:
                self.user_data[user_id]['waiting_for_count'] = False
            await self._cmd_start(message)
        else:
            await message.reply("❓ Используйте кнопки меню или команды:\n/start - главное меню\n/help - помощь\n\nИли отправьте ссылку на категорию Ozon для начала парсинга.")
    
    async def _start_parsing_flow_from_keyboard(self, message: Message):
        if self.app_manager.is_running:
            keyboard = ReplyKeyboardMarkup(keyboard=[
                [KeyboardButton(text="❌ Завершить"), KeyboardButton(text="🏠 Главное меню")]
            ], resize_keyboard=True)
            await message.reply("⚠️ Парсинг уже запущен. Дождитесь завершения или остановите его.", reply_markup=keyboard)
        else:
            keyboard = ReplyKeyboardMarkup(keyboard=[
                [KeyboardButton(text="❌ Отмена")]
            ], resize_keyboard=True)
            await message.reply("🔗 Отправьте ссылку на категорию Ozon:", reply_markup=keyboard)
            state = FSMContext(storage=self.dp.storage, key=f"user:{message.from_user.id}")
            await state.set_state(ParsingStates.waiting_for_url)
    
    def _is_authorized_user(self, message_or_query) -> bool:
        user_id = str(message_or_query.from_user.id)
        if user_id not in self.user_ids:
            logger.warning(f"Неавторизованный пользователь {user_id} пытается использовать бота")
            return False
        return True

    async def _reply_to(self, message_or_query, text: str, **kwargs):
        if isinstance(message_or_query, CallbackQuery):
            await message_or_query.message.reply(text, **kwargs)
        else:
            await message_or_query.reply(text, **kwargs)
    
    def _is_ozon_category_url(self, url: str) -> bool:
        return (
            url.startswith(('http://', 'https://')) and
            'ozon.ru' in url and
            ('/category/' in url or '/search/' in url or '/seller/' in url)
        )
    
    async def send_message(self, text: str) -> bool:
        try:
            if not self.is_running:
                return False
            
            # Отправляем сообщение всем авторизованным пользователям
            success = True
            for user_id in self.user_ids:
                try:
                    await self.bot.send_message(chat_id=user_id, text=text)
                except Exception as e:
                    logger.error(f"Ошибка отправки сообщения пользователю {user_id}: {e}")
                    success = False
            
            return success
            
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения в Telegram: {e}")
            return False
    
    def send_message_sync(self, text: str, user_id: str = None) -> bool:
        """Thread-safe метод для отправки сообщений из других потоков"""
        try:
            if not self.is_running:
                return False
            
            # Создаем новый event loop для этого потока
            import asyncio
            
            async def _send():
                success = True
                target_user_ids = [str(user_id)] if user_id else self.user_ids
                for target_user_id in target_user_ids:
                    try:
                        # Создаем новый бот для отправки сообщения
                        temp_bot = Bot(token=self.bot_token)
                        await temp_bot.send_message(chat_id=target_user_id, text=text)
                        await temp_bot.session.close()
                    except Exception as e:
                        logger.error(f"Ошибка отправки сообщения пользователю {target_user_id}: {e}")
                        success = False
                return success
            
            # Запускаем в новом event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(_send())
                return result
            finally:
                loop.close()
                
        except Exception as e:
            logger.error(f"Ошибка синхронной отправки сообщения: {e}")
            return False
    

    
    async def _change_default_count(self, query: CallbackQuery, state: FSMContext):
        keyboard = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="❌ Отмена")]
        ], resize_keyboard=True)
        
        await query.message.edit_text("🔢 Введите новое количество товаров по умолчанию (от 1 до 10000):")
        await query.message.reply("Или нажмите кнопку:", reply_markup=keyboard)
        await state.set_state(ParsingStates.waiting_for_default_count)
    
    async def _handle_default_count_input(self, message: Message, state: FSMContext):
        if not self._is_authorized_user(message):
            return
        
        if message.text == "❌ Отмена":
            await self._show_settings(message, state)
            return
        
        try:
            count = int(message.text.strip())
            if count < 1 or count > 10000:
                raise ValueError()
        except ValueError:
            keyboard = ReplyKeyboardMarkup(keyboard=[
                [KeyboardButton(text="❌ Отмена")]
            ], resize_keyboard=True)
            await message.reply("❌ Введите число от 1 до 10000:", reply_markup=keyboard)
            return
        
        user_id = str(message.from_user.id)
        settings = self.db.get_user_settings(user_id)
        settings['default_product_count'] = count
        self.db.save_user_settings(user_id, settings['selected_fields'], settings['field_order'], count)
        
        keyboard = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="🏠 Главное меню")]
        ], resize_keyboard=True)
        
        await message.reply(f"✅ Количество товаров по умолчанию изменено на {count}", reply_markup=keyboard)
        await state.clear()
    
    async def _configure_fields(self, query: CallbackQuery, state: FSMContext):
        user_id = str(query.from_user.id)
        settings = self.db.get_user_settings(user_id)
        selected_fields = settings['selected_fields']
        field_order = settings['field_order']
        
        text = "📝 <b>Настройка полей экспорта</b>\n\n"
        text += "Выберите поля для экспорта в Excel:\n\n"
        
        keyboard = []
        for field_key, field_name in FIELD_NAMES.items():
            if field_key in selected_fields:
                order_num = field_order.index(field_key) + 1 if field_key in field_order else len(field_order) + 1
                button_text = f"✅ {field_name} ({order_num})"
            else:
                button_text = f"⬜ {field_name}"
            
            keyboard.append([InlineKeyboardButton(text=button_text, callback_data=f"toggle_field_{field_key}")])
        
        keyboard.extend([
            [InlineKeyboardButton(text="💾 Сохранить", callback_data="save_settings")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="settings")]
        ])
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await query.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    

    
    def stop(self):
        try:
            self.is_running = False
            # 🚀 non-blocking stop
            if hasattr(self, 'dp') and self.dp and self.dp._loop and not self.dp._loop.is_closed():
                asyncio.run_coroutine_threadsafe(self.dp.stop_polling(), self.dp._loop)
            if self.bot_thread and self.bot_thread.is_alive():
                self.bot_thread.join(timeout=2)
        except Exception as e:
            logger.error(f"Ошибка остановки Telegram бота: {e}")
