import asyncio
import time
import random
import logging
from pywebio import start_server
from pywebio.input import *
from pywebio.output import *
from pywebio.session import get_info, defer_call, info as session_info, run_async, run_js

# Настройка логирования
logging.basicConfig(
    filename="chat_log.txt",  # Файл для записи логов
    level=logging.INFO,       # Уровень логирования
    format="%(asctime)s - %(levelname)s - %(message)s"  # Формат записи
)

# Файл для хранения истории чата
CHAT_HISTORY_FILE = "chat_history.txt"

# Чтение истории чата при запуске
try:
    with open(CHAT_HISTORY_FILE, "r", encoding="utf-8") as file:
        chat_msgs = [tuple(line.strip().split(":", 1)) for line in file.readlines()]
except FileNotFoundError:
    chat_msgs = []

online_users = set()
user_last_msg_time = {}  # Словарь для отслеживания времени последнего сообщения
muted_users = {}  # Словарь для хранения времени разблокировки мута
user_violations = {}  # Словарь для отслеживания количества нарушений
blocked_ips = {}  # Словарь для временно заблокированных IP-адресов (ключ: IP, значение: время разблокировки)
MAX_MESSAGES_COUNT = 200
SPAM_PROTECTION_INTERVAL = 2  # Минимальное время между сообщениями (в секундах)
IP_BLOCK_DURATION = 60 * 5  # Временная блокировка IP-адреса (в секундах)

# Список запрещённых слов
PROHIBITED_WORDS = [
    "пример1", "пример2", "пример3", "пример4"
]

# Список запрещённых никнеймов (чёрный список)
BLACKLIST_NICKNAMES = [
    "admin", "root", "system", "broadcast", "server", "guest", "anonymous",
    "bot", "spammer", "virus", "hacker", "📢", "⚠️", "❌"
]

# Функция для записи сообщения в файл
def save_message_to_file(nickname, message):
    with open(CHAT_HISTORY_FILE, "a", encoding="utf-8") as file:
        file.write(f"{nickname}:{message}\n")

async def main():
    global chat_msgs
    
    # Получаем IP-адрес пользователя
    user_ip = get_info().client_ip
    nickname = None  # Для логирования при выходе
    
    # Проверяем, заблокирован ли IP-адрес
    if user_ip in blocked_ips and blocked_ips[user_ip] > time.time():
        remaining_time = int(blocked_ips[user_ip] - time.time())
        toast(f"❌ Ваш IP-адрес заблокирован. Осталось {remaining_time} секунд.", color="error")
        logging.warning(f"Попытка входа с заблокированного IP: {user_ip}")
        return
    
    put_markdown("## 🧊 Добро пожаловать в онлайн чат!\nИсходный код данного чата укладывается в 177 строк кода!")
    msg_box = output()
    put_scrollable(msg_box, height=300, keep_bottom=True)
    
    # Отображаем историю чата при входе
    for nick, msg in chat_msgs[-MAX_MESSAGES_COUNT:]:
        msg_box.append(put_markdown(f"{nick}: {msg}"))
    
    # Валидация никнейма с проверкой на уникальность и чёрный список
    nickname = await input("Войти в чат", required=True, placeholder="Ваше имя", 
                           validate=lambda n: (
                               "Такой ник уже используется!" if n in online_users else 
                               "Этот ник запрещён!" if n.lower() in BLACKLIST_NICKNAMES else 
                               None
                           ))
    
    # Логируем подключение пользователя
    logging.info(f"Пользователь {nickname} ({user_ip}) присоединился к чату.")
    
    online_users.add(nickname)
    chat_msgs.append(('📢', f'{nickname} присоединился к чату!'))
    msg_box.append(put_markdown(f'📢 {nickname} присоединился к чату'))
    save_message_to_file('📢', f'{nickname} присоединился к чату!')
    
    refresh_task = run_async(refresh_msg(nickname, msg_box))
    
    try:
        while True:
            data = await input_group("💭 Новое сообщение", [
                input(placeholder="Текст сообщения ...", name="msg"),
                actions(name="cmd", buttons=["Отправить", {'label': "Выйти из чата", 'type': 'cancel'}])
            ], validate=lambda m: ('msg', "Введите текст сообщения!") if m["cmd"] == "Отправить" and not m['msg'] else None)
            
            if data is None:
                break
            
            current_time = time.time()
            last_msg_time = user_last_msg_time.get(nickname, 0)
            
            if current_time - last_msg_time < SPAM_PROTECTION_INTERVAL:
                toast("❌ Вы отправляете сообщения слишком часто. Подождите немного.", color="error")
                continue
            
            # Проверка на мут
            if nickname in muted_users and muted_users[nickname] > current_time:
                remaining_time = int(muted_users[nickname] - current_time)
                toast(f"❌ Вы в муте! Осталось {remaining_time} секунд.", color="error")
                continue
            
            # Проверка на запрещённые слова
            message = data['msg'].lower()  # Преобразуем сообщение в нижний регистр для регистронезависимой проверки
            if any(word in message for word in PROHIBITED_WORDS):
                # Генерируем случайное время мута (от 30 секунд до 1 минуты)
                mute_duration = random.randint(10, 120)
                muted_users[nickname] = current_time + mute_duration
                
                # Увеличиваем счётчик нарушений
                user_violations[nickname] = user_violations.get(nickname, 0) + 1
                
                if user_violations[nickname] >= 3:
                    # Исключаем пользователя и блокируем IP-адрес на определённое время
                    block_until = time.time() + IP_BLOCK_DURATION
                    blocked_ips[user_ip] = block_until
                    toast(f"❌ Вы были исключены из чата за многочисленные нарушения! Ваш IP-адрес заблокирован на {IP_BLOCK_DURATION // 60} минут.", color="error")
                    
                    # Логируем исключение пользователя
                    logging.error(f"Пользователь {nickname} ({user_ip}) был исключён за нарушения правил.")
                    break
                else:
                    toast(f"❌ Ваше сообщение содержит запрещённые слова! Вы получили мут на {mute_duration} секунд. ({user_violations[nickname]}/3 нарушений)", color="error")
                
                continue
            
            user_last_msg_time[nickname] = current_time  # Обновляем время последнего сообщения
            
            # Добавляем сообщение в чат и сохраняем его в файл
            msg_box.append(put_markdown(f"{nickname}: {data['msg']}"))
            chat_msgs.append((nickname, data['msg']))
            save_message_to_file(nickname, data['msg'])
    
    finally:
        # Логируем отключение пользователя
        if nickname:
            logging.info(f"Пользователь {nickname} ({user_ip}) покинул чат.")
        
        refresh_task.close()
        if nickname in online_users:
            online_users.remove(nickname)
        toast("Вы вышли из чата!")
        msg_box.append(put_markdown(f'📢 Пользователь {nickname} покинул чат!'))
        chat_msgs.append(('📢', f'Пользователь {nickname} покинул чат!'))
        save_message_to_file('📢', f'Пользователь {nickname} покинул чат!')
        put_buttons(['Перезайти'], onclick=lambda btn: run_js('window.location.reload()'))

async def refresh_msg(nickname, msg_box):
    global chat_msgs
    last_idx = len(chat_msgs)
    while True:
        await asyncio.sleep(1)
        
        for m in chat_msgs[last_idx:]:
            if m[0] != nickname:  # Если сообщение не от текущего пользователя
                msg_box.append(put_markdown(f"{m[0]}: {m[1]}"))
        
        # Очищаем старые сообщения
        if len(chat_msgs) > MAX_MESSAGES_COUNT:
            chat_msgs = chat_msgs[len(chat_msgs) // 2:]
        
        last_idx = len(chat_msgs)

if __name__ == "__main__":
    start_server(main, debug=True, port=8080, cdn=False)