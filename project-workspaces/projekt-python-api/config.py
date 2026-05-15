from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Multi-User Blog API"
    debug: bool = False

    database_url: str = "postgresql+psycopg2://blog_user:blog_pass@localhost:5432/blog_db"
    secret_key: str = "change-me-to-a-real-secret-key-in-production"
    algorithm: str = "HS256"
    access_token_expire_seconds: int = 3600

    class Config:
        env_file = ".env"


settings = Settings()
