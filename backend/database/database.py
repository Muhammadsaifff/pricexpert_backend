from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base
import os
from dotenv import load_dotenv

# Load .env
load_dotenv()

# -----------------------------
# 1️⃣ DATABASE CONFIG
# -----------------------------

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable not found")

print("Database configuration loaded.")

# -----------------------------
# 2️⃣ ENGINE (Neon + Render safe)
# -----------------------------

engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=300,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# -----------------------------
# 3️⃣ BASE MODEL
# -----------------------------

Base = declarative_base()

# -----------------------------
# 4️⃣ FASTAPI DB DEPENDENCY
# -----------------------------

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# -----------------------------
# 5️⃣ INIT DB
# -----------------------------

def init_db():
    """
    Create tables + lightweight migrations
    """
    Base.metadata.create_all(bind=engine)
    _apply_postgres_schema_updates()

# -----------------------------
# 6️⃣ LIGHTWEIGHT MIGRATIONS
# -----------------------------

def _apply_postgres_schema_updates():
    try:
        inspector = inspect(engine)

        # If users table doesn't exist, skip
        if "users" not in inspector.get_table_names():
            return

        columns = {col["name"] for col in inspector.get_columns("users")}

        # Add missing password column if needed
        if "password" not in columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE users ADD COLUMN password VARCHAR")
                )
            print("Schema update applied: users.password added")

    except Exception as e:
        print(f"Schema update skipped due to error: {e}")