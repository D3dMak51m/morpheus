import asyncio
import httpx
from app.database import get_db, SessionLocal
from app.models import AdminUser
from app.rbac import create_access_token

async def main():
    db = SessionLocal()
    user = db.query(AdminUser).first()
    token = create_access_token(user)
    db.close()

    headers = {"Authorization": f"Bearer {token}"}

    # 1. Generate Profile
    seed = {
        'agent_id': 'my_main_agent',
        'caste': 'alpha',
        'codename': 'Omega',
        'focus': 'Uzbek Nationalist, Modernist, panturkism, national traditions-first. Anti-russia',
        'platforms': ['telegram']
    }
    
    print("Generating profile...")
    async with httpx.AsyncClient(timeout=150.0) as client:
        try:
            res = await client.post('http://localhost:8000/api/v1/souls/genesis', json=seed, headers=headers)
            print("Genesis Response:", res.status_code, res.text)
        except Exception as e:
            print("Genesis Error:", e)

    # 2. Request OTP for Telegram
    auth_data = {
        "api_id": 34446965,
        "api_hash": "fbd29ec8597cfba6f3c345c77282ff01",
        "phone_number": "+998942577001"
    }
    print("Requesting Telegram OTP...")
    async with httpx.AsyncClient() as client:
        res = await client.post('http://localhost:8000/api/v1/auth-factory/telegram/request-code', json=auth_data, headers=headers)
        print("OTP Response:", res.status_code, res.text)

if __name__ == '__main__':
    asyncio.run(main())
