from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PROJECT_NAME: str = "Crew Travel Buddy"
    WHATSAPP_VERIFY_TOKEN: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_APP_SECRET: str = ""
    OPENAI_API_KEY: str = ""  # required: every reply comes from OpenAI
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_GUARD_MODEL: str = "gpt-4o-mini"  # the strict travel-only topic check that runs on every message
    OPENAI_TRANSCRIBE_MODEL: str = "whisper-1"  # reads the speech in a shared reel
    # rzp_test_... while testing, rzp_live_... for real money. Common alternative names are accepted too.
    RAZORPAY_KEY_ID: str = Field("", validation_alias=AliasChoices("RAZORPAY_KEY_ID", "NEXT_PUBLIC_RAZORPAY_KEY_ID", "RAZORPAY_KEY", "RAZORPAY_API_KEY"))
    RAZORPAY_KEY_SECRET: str = Field("", validation_alias=AliasChoices("RAZORPAY_KEY_SECRET", "RAZORPAY_SECRET", "RAZORPAY_API_SECRET"))
    RAZORPAY_WEBHOOK_SECRET: str = ""  # set in Razorpay dashboard -> Webhooks; used to verify callbacks
    BUNDLE_DISCOUNT_PCT: int = 0  # % off a trip that combines 2+ kinds (flight + hotel...); 0 = no discount
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
