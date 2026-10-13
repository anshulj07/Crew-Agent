"""Data access: users, conversation state, chat history and paid orders. Synchronous; callers run it in a thread."""
import random
import string
from datetime import datetime, timezone


class CoreRepo:
    def __init__(self, client):
        self.db = client

    # ---- users / conversation state
    def get_or_create_user(self, phone: str, name: str) -> dict:
        for _ in range(2):  # second pass covers a concurrent insert of the same phone
            rows = self.db.table("users").select("*").eq("phone", phone).limit(1).execute().data
            if rows:
                return rows[0]
            try:
                return self.db.table("users").insert(
                    {"phone": phone, "name": name or "Traveller", "preferences": {}}
                ).execute().data[0]
            except Exception:
                continue
        raise RuntimeError("Could not create user")

    def get_conversation(self, phone: str) -> dict | None:
        rows = self.db.table("conversations").select("*").eq("phone", phone).limit(1).execute().data
        return rows[0] if rows else None

    def save_conversation(self, phone: str, step: str, context: dict) -> None:
        self.db.table("conversations").upsert(
            {"phone": phone, "current_step": step, "context": context}, on_conflict="phone"
        ).execute()

    # ---- chat memory
    def log_message(self, user_id: str, role: str, agent: str | None, content: str) -> None:
        self.db.table("messages").insert(
            {"user_id": user_id, "role": role, "agent": agent, "content": content[:2000]}
        ).execute()

    def recent_messages(self, user_id: str, limit: int = 8, since: str | None = None) -> list[dict]:
        """The last `limit` messages, oldest first; `since` (ISO time) leaves out everything before a "new trip"."""
        query = self.db.table("messages").select("role, agent, content").eq("user_id", user_id)
        if since:
            query = query.gt("created_at", since)
        rows = query.order("created_at", desc=True).limit(limit).execute().data
        return rows[::-1]  # oldest first

    # ---- places the reel planner recognises
    def list_airports(self) -> list[dict]:
        return self.db.table("airports").select("*").execute().data

    # ---- orders: the user's trip, paid with one link
    def create_order(self, user_id: str, phone: str, items: list[dict], amount: float, currency: str) -> dict:
        ref = "CR" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        return self.db.table("orders").insert({
            "ref": ref, "user_id": user_id, "phone": phone, "items": items, "amount": amount, "currency": currency,
        }).execute().data[0]

    def set_order_link(self, order_id: str, link_id: str, short_url: str) -> None:
        self.db.table("orders").update({"link_id": link_id, "short_url": short_url}).eq("id", order_id).execute()

    def cancel_order(self, order_id: str) -> None:
        """The trip changed and got a new link; this one is replaced (still marked paid if its link gets paid anyway)."""
        self.db.table("orders").update({"status": "cancelled"}).eq("id", order_id).eq("status", "created").execute()

    def mark_order_paid(self, link_id: str) -> dict | None:
        """The order this link paid, or None if it is unknown or was already marked paid (Razorpay retries webhooks)."""
        rows = (self.db.table("orders").update({"status": "paid", "paid_at": datetime.now(timezone.utc).isoformat()})
                .eq("link_id", link_id).in_("status", ["created", "cancelled"]).execute().data)
        return rows[0] if rows else None
