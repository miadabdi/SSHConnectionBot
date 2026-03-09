import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass(slots=True)
class Settings:
    bot_token: str = ""
    mongo_uri: str = "mongodb://mongo:27017/ssh_bot?authSource=admin"
    mongo_db_name: str = "ssh_bot"
    encryption_key: str = ""
    allowed_users: list[int] = field(default_factory=list)
    session_timeout_minutes: int = 30
    stream_update_interval: float = 0.3

    @classmethod
    def from_env(cls) -> "Settings":
        raw_users = os.getenv("ALLOWED_USERS", "").strip()
        allowed_users = [int(u.strip()) for u in raw_users.split(",") if u.strip()] if raw_users else []

        return cls(
            bot_token=os.getenv("BOT_TOKEN", ""),
            mongo_uri=os.getenv("MONGO_URI", "mongodb://mongo:27017/ssh_bot?authSource=admin"),
            mongo_db_name=os.getenv("MONGO_DB_NAME", "ssh_bot"),
            encryption_key=os.getenv("ENCRYPTION_KEY", ""),
            allowed_users=allowed_users,
            session_timeout_minutes=int(os.getenv("SESSION_TIMEOUT", "30")),
            stream_update_interval=float(os.getenv("STREAM_UPDATE_INTERVAL", "0.3")),
        )

    def validate(self) -> None:
        missing = []
        if not self.bot_token:
            missing.append("BOT_TOKEN")
        if not self.encryption_key:
            missing.append("ENCRYPTION_KEY")
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


settings = Settings.from_env()
