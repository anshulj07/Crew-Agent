"""The travel buddy: every message goes to OpenAI and its answer goes back to the user. Done in code: payment and the trip.

Each option the model shows is its own card with a Book button. A tap adds it to the user's trip (flight + hotel + car...)
and sends ONE Razorpay link for the whole trip; adding more replaces that link with one for the new total, so everything
is paid together. Right after, the model is asked for the next thing the trip needs, so every booking is a chance to sell
the rest. The webhook confirms the payment.

When the user asks for the link ("send me the payment link", "there's no link"), the model says so with
action="send_payment_link" and the app re-sends the real Pay button (a fresh link if the last one expired); the model
never writes links itself.

New trip: when the trip's main city changes (Paris -> Istanbul) the user is asked once, with buttons, whether it's a new
trip; "new trip" / "start over" (the model's action="new_trip") resets straight away. A reset retires the unpaid trip and
its link and makes the model forget the old chat (ctx["since"]), so old dates and cities don't leak into the new trip.

Button ids: pay:<batch>:<n> (a card), trip:vacation / trip:business (the trip purpose), newtrip:yes / newtrip:no.
State: ctx["offers"] = the last few batches of cards [{"id", "items"}], so a tap on a recent card still works;
ctx["trip_cart"] = {"items", "order_id", "link_id", "url", "ref", "at"}; ctx["purpose"], ctx["purpose_asked"];
ctx["trip_places"] (the trip's cities), ctx["pending_trip"] = {"text", "dest"} (waiting for the new-trip answer), ctx["since"].
"""
import asyncio
import logging
import secrets
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from app.agents.base import Agent, Session
from app.agents.travel.brain import CRISIS, PURPOSES, TRAVEL, Reply
from app.core.geo import fresh_location
from app.core.messages import buttons_msg, cta_msg, text_msg
from app.core.utils import now_ist

logger = logging.getLogger(__name__)
HISTORY_LIMIT = 12
LINK_MINUTES = 30
KEEP_BATCHES = 6
CARD_BODY = 1024  # WhatsApp's limit for a button message body
SYMBOLS = {"USD": "$"}
KIND_EMOJI = {"flight": "✈️", "hotel": "🏨", "car": "🚗", "transfer": "🚐", "activity": "🎟️", "cruise": "🚢"}
MSG_DOWN = "Sorry, I couldn't think that through just now. 🙏 Please send your message again in a moment."
MSG_OFF_TOPIC = ("I can only help with travel: flights ✈️, hotels 🏨, cars 🚕 and things to do 🎯. "
                 "Tell me where you're headed and I'll find you the best deals!")
MSG_CRISIS = ("I'm really sorry you're going through this. 💙 You don't have to face it alone. If you're in danger, please "
              "call your local emergency number right now. You can also reach a free, confidential helpline in your country "
              "at findahelpline.com.")
MSG_STALE = "That option is from an older search. Ask me again and I'll pull up fresh options. 🔄"
MSG_NO_PAYMENTS = "Online payment isn't set up yet, so I can't take this booking here right now. 🙏"
MSG_PAY_FAILED = "😕 I couldn't create the payment link. Please tap the option again in a minute."
PRICE_NOTE = "_Prices are indicative; our team confirms the booking after payment._"
MSG_EMPTY_TRIP = "Your trip is empty for now. Tap ✅ Book on anything you like and I'll send you the Pay button. 😊"
MSG_FRESH = "🆕 Fresh start! Your old trip is cleared."
TRIP_KEYS = ("trip_cart", "offers", "purpose", "purpose_asked", "trip_places", "pending_trip")  # what a new trip clears
MSG_PURPOSE = "Quick one so I pick the right stuff for you: is this a getaway or a work trip? 👇"


def money(amount: float, currency: str) -> str:
    n = f"{amount:,.0f}" if amount == int(amount) else f"{amount:,.2f}"
    return f"{SYMBOLS[currency]}{n}" if currency in SYMBOLS else f"{currency} {n}"


class TravelAgent(Agent):
    name = "travel"
    title = "Travel Buddy"
    emoji = "🧳"
    owns = frozenset({"pay", "trip", "newtrip"})

    def __init__(self, repo, brain, payments, bundle_pct: int = 0):
        self.repo = repo              # CoreRepo
        self.brain = brain            # OpenAIBrain (or a fake with the same respond method)
        self.payments = payments      # RazorpayService (or a fake)
        self.bundle_pct = bundle_pct  # % off a trip with 2+ kinds

    async def _db(self, fn, *args):
        return await asyncio.to_thread(fn, *args)

    def reset(self, s: Session) -> None:
        s.ctx.pop("offers", None)

    async def on_enter(self, s: Session) -> list[dict]:
        return await self.chat(s, "Hi")

    async def process(self, s: Session, text: str, reply_id: str | None) -> list[dict]:
        kind, _, val = (reply_id or "").partition(":")
        if kind == "pay":
            return await self._add_to_trip(s, reply_id)
        if kind == "trip" and val in PURPOSES:
            s.ctx["purpose"] = val
            return await self.chat(s, f"It's a {val} trip. Tailor my options to that.")
        if kind == "newtrip":
            return await self._answer_new_trip(s, val == "yes", text)
        return await self.chat(s, text)

    # ------------------------------------------------------------------ talking
    async def chat(self, s: Session, text: str, check_trip: bool = True) -> list[dict]:
        s.ctx["agent"], s.step = self.name, "chat"
        reply = await self._ask(s, text)
        if reply and reply.topic == TRAVEL and check_trip:
            if reply.action == "new_trip":  # "new trip", "start over": clear, then answer with a clean slate
                await self._reset(s)
                return [text_msg(MSG_FRESH)] + await self.chat(s, text, check_trip=False)
            places = s.ctx.get("trip_places") or []
            if reply.destination and places and reply.destination.lower() not in (p.lower() for p in places):
                s.ctx["pending_trip"] = {"text": text, "dest": reply.destination}
                return [buttons_msg(f"Quick check: is *{reply.destination}* a new trip, or part of your *{', '.join(places)}* trip? 👇",
                                    [("newtrip:yes", "🆕 New trip"), ("newtrip:no", "➕ Same trip")])]
        if reply and reply.topic == TRAVEL and reply.destination and not s.ctx.get("trip_places"):
            s.ctx["trip_places"] = [reply.destination]
        if not reply or (reply.topic == TRAVEL and not reply.text):
            return [text_msg(MSG_DOWN)]
        return await self._render(s, reply)

    async def _answer_new_trip(self, s: Session, new: bool, text: str) -> list[dict]:
        """The user's answer to "is Istanbul a new trip?": answer their message again, with or without the old trip."""
        pending = s.ctx.pop("pending_trip", None)
        if not pending:  # an old button: "New trip" still means start over
            if new:
                await self._reset(s)
                return [text_msg(f"{MSG_FRESH} Where to next? ✈️")]
            return await self.chat(s, "Let's carry on with my trip.")
        if new:
            await self._reset(s)
            s.ctx["trip_places"] = [pending["dest"]]
            return [text_msg(MSG_FRESH)] + await self.chat(s, pending["text"], check_trip=False)
        s.ctx["trip_places"] = [*(s.ctx.get("trip_places") or []), pending["dest"]]
        return await self.chat(s, pending["text"], check_trip=False)

    async def _reset(self, s: Session) -> None:
        """New trip: retire the unpaid trip and its link, forget its details, and hide the old chat from the model."""
        await self._replace(s.ctx.get("trip_cart") or {})
        for key in TRIP_KEYS:
            s.ctx.pop(key, None)
        s.ctx["since"] = datetime.now(timezone.utc).isoformat()

    async def _ask(self, s: Session, text: str) -> Reply | None:
        try:
            history = await self._db(self.repo.recent_messages, s.user["id"], HISTORY_LIMIT, s.ctx.get("since"))
        except Exception:
            logger.warning("Could not read chat history (is the `messages` table created?)")
            history = []
        items = (s.ctx.get("trip_cart") or {}).get("items") or []
        try:
            return await self.brain.respond(name=s.user.get("name") or "Traveller", today=now_ist().date(),
                                            location=fresh_location(s.ctx), history=history, text=text or "Hi",
                                            phone=s.phone, purpose=s.ctx.get("purpose"), trip=items, bundle_pct=self.bundle_pct,
                                            trip_total=self._totals(items)[3] if items else None,
                                            places=s.ctx.get("trip_places"))
        except Exception:
            logger.exception("OpenAI call failed")
            return None

    async def _render(self, s: Session, reply: Reply) -> list[dict]:
        if reply.topic == CRISIS:
            logger.warning("Crisis message from user %s", s.user["id"])  # no message content in logs
            return [text_msg(MSG_CRISIS)]
        if reply.topic != TRAVEL:  # a fixed answer, never the model's: nothing off topic gets through
            return [text_msg(MSG_OFF_TOPIC)]
        if reply.purpose in ("vacation", "business"):
            s.ctx["purpose"] = reply.purpose
        out = [text_msg(reply.text + (f"\n\n{PRICE_NOTE}" if reply.options else ""))]
        if reply.options:  # one card per option, its Book button right under it
            batch = secrets.token_hex(3)
            offers = [b for b in s.ctx.get("offers") or [] if isinstance(b, dict) and "id" in b]
            s.ctx["offers"] = (offers + [{"id": batch, "items": [asdict(o) for o in reply.options]}])[-KEEP_BATCHES:]
            out += [buttons_msg(self._card(i, o), [(f"pay:{batch}:{i}", f"✅ Book · {money(o.price, o.currency)}")])
                    for i, o in enumerate(reply.options)]
            if not s.ctx.get("purpose") and not s.ctx.get("purpose_asked") and {o.kind for o in reply.options} & {"flight", "hotel"}:
                s.ctx["purpose_asked"] = True
                out.append(buttons_msg(MSG_PURPOSE, [("trip:vacation", "🏖️ Vacation"), ("trip:business", "💼 Business")]))
        if reply.action == "send_payment_link":
            out += await self._pay_button(s)
        return out

    @staticmethod
    def _card(i: int, o) -> str:
        """Title, the model's card lines, and the Expedia link last (the details are trimmed first, never the link)."""
        head, tail = f"*{i + 1}. {o.title}*\n", f"\n🔎 See it on Expedia: {o.link}" if o.link else ""
        return head + o.card[:CARD_BODY - len(head) - len(tail)] + tail

    # ------------------------------------------------------------------ the trip and its payment
    def _find_offer(self, s: Session, reply_id: str) -> dict | None:
        _, batch, idx = (reply_id.split(":") + ["", ""])[:3]
        for b in s.ctx.get("offers") or []:
            if isinstance(b, dict) and b.get("id") == batch and idx.isdigit() and int(idx) < len(b.get("items", [])):
                return b["items"][int(idx)]
        return None

    def _totals(self, items: list[dict]) -> tuple[float, int, float, float]:
        """(subtotal, discount %, discount, total). The bundle discount needs two or more kinds in the trip."""
        subtotal = round(sum(i["price"] for i in items), 2)
        pct = self.bundle_pct if len({i.get("kind") for i in items}) >= 2 else 0
        discount = round(subtotal * pct / 100, 2)
        return subtotal, pct, discount, round(subtotal - discount, 2)

    async def _add_to_trip(self, s: Session, reply_id: str) -> list[dict]:
        item = self._find_offer(s, reply_id)
        if item is None:
            return [text_msg(MSG_STALE)]
        if not self.payments.configured():
            return [text_msg(MSG_NO_PAYMENTS)]
        item = {"kind": item.get("kind") or "hotel", "title": item["title"], "card": item.get("card") or item.get("subtitle") or "",
                "price": item["price"], "currency": item["currency"]}
        cart = s.ctx.get("trip_cart") or {"items": []}
        if any(i["title"] == item["title"] and i["price"] == item["price"] for i in cart["items"]):
            return await self._pay_button(s)  # tapped twice: same trip, same (or a refreshed) link
        cart = await self._checkout(s, cart["items"] + [item])
        if cart is None:
            return [text_msg(MSG_PAY_FAILED)]
        out = [self._trip_msg(cart)]
        nxt = await self._ask(s, f"(I added {item['title']} ({item['kind']}) to my trip. If there's something that would genuinely "
                                 "make this trip easier or better, show me options for it and tell me why, like a friend would.)")
        return out + (await self._render(s, nxt) if nxt else [])

    async def _pay_button(self, s: Session) -> list[dict]:
        """The real Pay button for the user's trip, with a fresh link if the last one expired."""
        cart = s.ctx.get("trip_cart") or {}
        if not cart.get("items"):
            return [text_msg(MSG_EMPTY_TRIP)]
        if not self.payments.configured():
            return [text_msg(MSG_NO_PAYMENTS)]
        if not self._link_alive(cart):
            cart = await self._checkout(s, cart["items"])
            if cart is None:
                return [text_msg(MSG_PAY_FAILED)]
        return [self._trip_msg(cart)]

    @staticmethod
    def _link_alive(cart: dict) -> bool:
        try:
            made = datetime.fromisoformat(cart["at"])
        except (KeyError, TypeError, ValueError):
            return False
        return bool(cart.get("url")) and now_ist() - made < timedelta(minutes=LINK_MINUTES - 2)

    async def _checkout(self, s: Session, items: list[dict]) -> dict | None:
        """One order and one Razorpay link for the whole trip; the previous link is retired. None if it failed."""
        _, _, _, total = self._totals(items)
        cur = items[0]["currency"]
        try:
            order = await self._db(self.repo.create_order, s.user["id"], s.phone, items, total, cur)
            desc = f"Trip {order['ref']}: " + ", ".join(i["title"] for i in items)
            link = await self.payments.create_payment_link(total, cur, order["ref"], desc, s.phone, s.user.get("name") or "", LINK_MINUTES)
            await self._db(self.repo.set_order_link, order["id"], link["id"], link["short_url"])
        except Exception:
            logger.exception("Could not create a payment link")
            return None
        await self._replace(s.ctx.get("trip_cart") or {})
        s.ctx["trip_cart"] = {"items": items, "order_id": order["id"], "link_id": link["id"], "url": link["short_url"],
                              "ref": order["ref"], "at": now_ist().isoformat()}
        return s.ctx["trip_cart"]

    async def _replace(self, old: dict) -> None:
        """The trip got a new link: retire the old one so only the latest total can be paid. Best effort."""
        if not old.get("order_id"):
            return
        try:
            await self._db(self.repo.cancel_order, old["order_id"])
            await self.payments.cancel_payment_link(old["link_id"])
        except Exception:
            logger.warning("Could not cancel the replaced payment link %s", old.get("link_id"))

    def _trip_msg(self, cart: dict) -> dict:
        items, cur = cart["items"], cart["items"][0]["currency"]
        subtotal, pct, discount, total = self._totals(items)
        lines = ["🧳 *Your trip*"] + [f"{KIND_EMOJI.get(i['kind'], '•')} {i['title']}: {money(i['price'], cur)}" for i in items]
        if discount:
            lines.append(f"🎁 Bundle discount ({pct}%): −{money(discount, cur)}")
        lines += ["", f"Total: *{money(total, cur)}* · Ref {cart['ref']}"]
        if self.bundle_pct and not discount:
            lines.append(f"💡 Tip: add your hotel or car here too and you save *{self.bundle_pct}%* on the whole trip.")
        lines += ["", f"One payment covers everything, and we'll make sure it all lines up for you. 😊 The link is valid for "
                      f"{LINK_MINUTES} min; add anything else and I'll update the total."]
        return cta_msg("\n".join(lines), f"💳 Pay {money(total, cur)}", cart["url"])

    async def confirm_payment(self, link_id: str) -> tuple[str, list[dict]] | None:
        """Razorpay webhook: (WhatsApp number, messages) for a newly paid order, or None if already handled."""
        order = await self._db(self.repo.mark_order_paid, link_id)
        if order is None:
            return None
        cur = order["currency"]
        lines = [f"{KIND_EMOJI.get(i.get('kind'), '•')} {i['title']}" for i in order.get("items") or []]
        return order["phone"].lstrip("+"), [text_msg(
            f"✅ Payment received: *{money(float(order['amount']), cur)}*\nRef: *{order['ref']}*\n" + "\n".join(lines) +
            "\n\nYou're all set! 🙌 Our team is confirming everything and will message you here shortly. Have an amazing trip! ✨")]
