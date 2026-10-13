from fastapi import FastAPI
from app.core.config import settings
from app.api.routes import razorpay, whatsapp

app = FastAPI(title=settings.PROJECT_NAME)

app.include_router(whatsapp.router, prefix="/webhook", tags=["whatsapp"])
app.include_router(razorpay.router, prefix="/webhook", tags=["razorpay"])

@app.get("/")
def read_root():
    return {"message": f"{settings.PROJECT_NAME} is running!"}
