from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAN_", extra="ignore")
    database_url: str = "postgresql+psycopg://localhost/can"
    database_host: str = ""
    database_port: int = 5432
    database_name: str = "can"
    database_user: str = "can"
    database_password_file: str = ""
    environment: str = "production"
    recovery_mode: Literal["off_host", "single_server"] = "off_host"
    apple_client_id: str = ""
    apple_team_id: str = ""
    apple_key_id: str = ""
    apple_private_key: str = ""
    encryption_key: str = ""
    invitation_hmac_key: str = ""
    encryption_key_file: str = ""
    invitation_hmac_key_file: str = ""
    apple_private_key_file: str = ""
    apns_private_key: str = ""
    apns_private_key_file: str = ""
    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_app_topic: str = ""
    apns_widget_topic: str = ""
    deletion_journal_dir: str = ""
    access_minutes: int = 15
    refresh_days: int = 30
    session_days: int = 180
    widget_days: int = 7

    @model_validator(mode="after")
    def mounted_secrets(self) -> "Settings":
        for name in (
            "encryption_key",
            "invitation_hmac_key",
            "apple_private_key",
            "apns_private_key",
        ):
            path = getattr(self, name + "_file")
            if path:
                setattr(self, name, Path(path).read_text().strip())
        if self.database_host:
            password = (
                Path(self.database_password_file).read_text().strip()
                if self.database_password_file
                else None
            )
            self.database_url = URL.create(
                "postgresql+psycopg",
                username=self.database_user,
                password=password,
                host=self.database_host,
                port=self.database_port,
                database=self.database_name,
            ).render_as_string(hide_password=False)
        if self.environment not in ("production", "staging"):
            raise ValueError("invalid environment")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
