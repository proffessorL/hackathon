import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "google")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gemini-2.5-flash")

settings = Settings()
