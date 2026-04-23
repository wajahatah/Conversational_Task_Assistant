import asyncio
import os
from dotenv import load_dotenv
from telegram import Bot

async def main():
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN not found in .env")
        return
    
    bot = Bot(token=token)
    try:
        me = await bot.get_me()
        print(f"Bot info: {me.username} (ID: {me.id})")
    except Exception as e:
        print(f"Failed to get bot info: {e}")

if __name__ == "__main__":
    asyncio.run(main())
