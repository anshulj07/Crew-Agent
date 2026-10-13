"""Razorpay webhook (payment_link.paid): mark the order paid and tell the user on WhatsApp."""
import json
import logging

from fastapi import APIRouter, HTTPException, Request

from app.api.routes.whatsapp import get_bot
from app.services.razorpay_service import RazorpayService
from app.services.whatsapp_service import WhatsAppService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/razorpay")
async def razorpay_webhook(request: Request):
    body = await request.body()
    if not RazorpayService.verify_webhook(body, request.headers.get("X-Razorpay-Signature", "")):
        raise HTTPException(status_code=400, detail="Bad signature")
    event = json.loads(body)
    if event.get("event") == "payment_link.paid":
        link_id = event["payload"]["payment_link"]["entity"]["id"]
        try:
            result = await get_bot().confirm_payment(link_id)
            if result:  # None means we had already handled it (Razorpay retries webhooks)
                number, messages = result
                for m in messages:
                    await WhatsAppService.send(number, m)
        except Exception:
            logger.exception("Could not confirm payment %s", link_id)
            raise HTTPException(status_code=500, detail="Try again")  # Razorpay will retry
    return {"status": "ok"}
