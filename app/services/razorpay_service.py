"""Razorpay payment links: create one per order, verify the webhook that says it was paid."""
import hashlib
import hmac
import time

import httpx

from app.core.config import settings

API = "https://api.razorpay.com/v1"
# Razorpay takes amounts in the currency's smallest unit: most have 2 decimals, these have 0 or 3.
DECIMALS = {"JPY": 0, "KRW": 0, "VND": 0, "CLP": 0, "ISK": 0, "UGX": 0, "XAF": 0, "XOF": 0,
            "BHD": 3, "JOD": 3, "KWD": 3, "OMR": 3}


def minor_units(amount: float, currency: str) -> int:
    return round(amount * 10 ** DECIMALS.get(currency, 2))


class RazorpayService:
    @staticmethod
    def configured() -> bool:
        return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)

    @staticmethod
    async def create_payment_link(amount: float, currency: str, reference_id: str, description: str, phone: str,
                                  name: str = "", expire_minutes: int = 30, client: httpx.AsyncClient | None = None) -> dict:
        """Returns {"id", "short_url"}. reference_id comes back in the webhook, so it ties a payment to an order.
        Currencies other than INR need international payments enabled on the Razorpay account."""
        payload = {
            "amount": minor_units(amount, currency),
            "currency": currency,
            "reference_id": reference_id,
            "description": description[:255],
            "customer": {"contact": phone, **({"name": name} if name else {})},
            "notify": {"sms": False, "email": False},  # we deliver the link on WhatsApp ourselves
            "expire_by": int(time.time()) + max(expire_minutes, 16) * 60,  # Razorpay needs at least 15 minutes
        }
        auth = (settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
        async with (client or httpx.AsyncClient(timeout=15)) as http:
            resp = await http.post(f"{API}/payment_links", json=payload, auth=auth)
            resp.raise_for_status()
            data = resp.json()
        return {"id": data["id"], "short_url": data["short_url"]}

    @staticmethod
    async def cancel_payment_link(link_id: str) -> None:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(f"{API}/payment_links/{link_id}/cancel", auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
            resp.raise_for_status()

    @staticmethod
    def verify_webhook(body: bytes, signature: str) -> bool:
        """Razorpay signs the raw request body with HMAC-SHA256 using the webhook secret."""
        if not settings.RAZORPAY_WEBHOOK_SECRET or not signature:
            return False
        expected = hmac.new(settings.RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
