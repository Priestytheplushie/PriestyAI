from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    GITHUB_TOKEN: str
    BOT_USERNAME: str = "PriestyAI"

    GITHUB_APP_ID: str
    GITHUB_APP_PRIVATE_KEY_PATH: str = "github_app.pem"
    GITHUB_WEBHOOK_SECRET: str = ""

    SMEE_URL: str

    GEMINI_API_KEY: str = ""
    GEMINI_API_KEY_2: str = ""
    GEMINI_API_KEY_3: str = ""
    GEMINI_API_KEY_4: str = ""

    PORT: int = 8000
    HOST: str = "0.0.0.0"

    @property
    def private_key_pem(self) -> str:
        pem_path = Path(self.GITHUB_APP_PRIVATE_KEY_PATH)
        if not pem_path.is_file():
            raise FileNotFoundError(
                f"GitHub App Private Key not found at {pem_path.resolve()}"
            )
        return pem_path.read_text(encoding="utf-8")


settings = Settings()
