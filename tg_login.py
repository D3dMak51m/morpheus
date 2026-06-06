import os
import asyncio
from pyrogram import Client
import psycopg2

DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "morpheus_db"
DB_USER = "morpheus_admin"
DB_PASSWORD = "morpheus_secure_pass"

# Using Official Telegram Android App keys as fallback
API_ID = 34446965
API_HASH = "fbd29ec8597cfba6f3c345c77282ff01" # +998942577001
# API_ID = 2040
# API_HASH = "b18441a1ff607e10a989891a5462e627"

async def main():
    print("=== Telegram MTProto Auth ===")
    print("Please make sure you put real API_ID and API_HASH in the script!")
    
    app = Client("my_account", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    
    await app.start()
    session_string = await app.export_session_string()
    print("\n✅ Authentication successful!")
    
    # Save to database
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        cur = conn.cursor()
        
        # Update the auth_cookies for agent 001
        cur.execute(
            "UPDATE souls_accounts SET auth_cookies = %s WHERE agent_id = '001' AND platform = 'telegram'",
            (session_string,)
        )
        conn.commit()
        
        if cur.rowcount > 0:
            print("✅ Session string successfully saved to 'souls_accounts' for Agent 001.")
        else:
            print("⚠️ Agent 001 for Telegram not found in database! Creating it...")
            cur.execute(
                "INSERT INTO souls_accounts (agent_id, platform, auth_cookies, status) VALUES ('001', 'telegram', %s, 'active')",
                (session_string,)
            )
            conn.commit()
            print("✅ Created new account record for Agent 001.")
            
        cur.close()
        conn.close()
        
    except Exception as e:
        print(f"❌ Failed to save to database: {e}")
        print(f"Here is your session string anyway:\n{session_string}")

if __name__ == "__main__":
    asyncio.run(main())
