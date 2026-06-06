# Сохраните это как test_myrmidon.py в корневой папке проекта
import json
import uuid
import subprocess

def send_task(platform: str, url: str, text: str):
    task = {
        "task_id": f"test-{uuid.uuid4().hex[:6]}",
        "agent_id": "001",
        "target_platform": platform,
        "action_type": "comment",
        "target_url": url,
        "text_to_publish": text,
        "parent_post_context": "test context",
        "execution_delay_sec": 1 # 1 секунда ожидания для быстрого теста
    }
    
    # Конвертируем в JSON строку
    payload = json.dumps(task)
    
    # Отправляем в Redis внутри докера
    cmd = [
        "docker", "exec", "morpheus-redis", 
        "redis-cli", "lpush", "queue:execution_tasks", payload
    ]
    
    subprocess.run(cmd)
    print(f"✅ Задача для {platform} отправлена! Ищите её в логах Myrmidon.")

if __name__ == "__main__":
    print("Отправляем тестовые задачи в очередь Myrmidon...")
    
    # Отправка задачи в Telegram. ВНИМАНИЕ: Telegram в нашей архитектуре (Stage 4) 
    # работает через API (Pyrogram), а НЕ через Appium (экран телефона).
    send_task("telegram", "@tashkent_news333", "Привет! Тестовый комментарий в Telegram.")
    
    # Отправка задачи в Instagram. Instagram работает через Appium и будет кликать по экрану
    # вашего подключенного устройства.
    send_task("instagram", "https://www.instagram.com/p/DKOgCPpsSl1PwpjLA21ZcKgzdNqgDItmU6fZHk0/?igsh=ajQxZWIxdTA5dHYz", "Привет! Тестовый комментарий в Instagram.")

