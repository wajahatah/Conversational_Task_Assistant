import asyncio
from app.database.session import engine
from sqlalchemy import text

async def check():
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT * FROM users"))
            users = result.fetchall()
            print(f"Users in DB: {users}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(check())
