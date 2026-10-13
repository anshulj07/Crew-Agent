"""The one entry point for every WhatsApp message.

It owns the conversation (state, dedupe, chat history) and picks who answers: the reel planner gets reel links, videos,
screenshots, its own buttons and the answers it asked for; everything else goes to the OpenAI travel buddy.
"""
import asyncio
import logging

from app.agents.base import Agent, Session
from app.core.geo import fresh_location, save_location
from app.core.lang import update_language
from app.core.messages import text_msg
from app.core.places import city
from app.core.utils import normalize_phone

logger = logging.getLogger(__name__)


class Bot:
    def __init__(self, repo, travel, planner: Agent):
        self.repo, self.travel, self.planner = repo, travel, planner

    async def _db(self, fn, *args):
        return await asyncio.to_thread(fn, *args)

    async def handle(self, wa_number: str, profile_name: str, msg_id: str, text: str, reply_id: str | None,
                     media: dict | None = None, location: dict | None = None) -> list[dict]:
        phone = normalize_phone(wa_number)
        if not phone:
            return [text_msg("Sorry, I couldn't read your phone number. Please message me from your WhatsApp number.")]

        convo = await self._db(self.repo.get_conversation, phone)
        ctx = dict((convo or {}).get("context") or {})
        if msg_id and ctx.get("last_msg_id") == msg_id:
            return []  # WhatsApp retried this webhook; already handled
        ctx["last_msg_id"] = msg_id
        if ctx.get("loc") and not fresh_location(ctx):
            ctx.pop("loc")  # a shared location is only kept for a few hours

        if text and not reply_id:
            update_language(ctx, text)  # answer in the language they write
        user = await self._db(self.repo.get_or_create_user, phone, profile_name)
        s = Session(phone, user, (convo or {}).get("current_step", "start"), msg_id, ctx)

        if location:
            loc = save_location(s.ctx, **location)
            text = f"📍 I'm here: {', '.join(x for x in (loc['name'], loc['address']) if x) or 'shared my location'}"
            out = await self.travel.chat(s, text)
        elif media:
            out = await self._on_media(s, media, text.strip())
        else:
            out = await self._dispatch(s, text.strip(), reply_id)
        out = out + await self._follow_up(s)
        await self._db(self.repo.save_conversation, s.phone, s.step, s.ctx)
        await self._remember(s, text, out)
        return out

    async def _dispatch(self, s: Session, text: str, reply_id: str | None) -> list[dict]:
        if reply_id:
            kind = reply_id.partition(":")[0]
            if kind in self.planner.owns:
                s.ctx["agent"] = self.planner.name
                return await self.planner.process(s, text, reply_id)
            if kind in self.travel.owns:
                return await self.travel.process(s, text, reply_id)
            if kind == "nav":  # the planner's "Menu" button: leave the reel and chat
                self.planner.reset(s)
            return await self.travel.chat(s, text)  # any other button: its title is what the user said
        if self.planner.claims(text) or (s.ctx.get("agent") == self.planner.name and self.planner.expects_text(s)):
            s.ctx["agent"] = self.planner.name
            return await self.planner.process(s, text, None)
        return await self.travel.chat(s, text)

    async def _on_media(self, s: Session, media: dict, caption: str) -> list[dict]:
        if self.planner.expects_media(s) or self.planner.accepts_media(media):
            s.ctx["agent"] = self.planner.name
            return await self.planner.on_media(s, media, caption)
        return await self.travel.chat(s, caption or f"(I sent you a {media.get('kind') or 'file'})")

    async def _follow_up(self, s: Session) -> list[dict]:
        """The planner's "Book this trip" asks for a follow-up: the travel buddy finds the bookings for that place."""
        fu = s.ctx.pop("follow_up", None)
        s.ctx.pop("queue", None)
        if not fu:
            return []
        dest = city((fu.get("slots") or {}).get("to") or "") or (s.ctx.get("plan") or {}).get("label") or "this place"
        days = (s.ctx.get("plan") or {}).get("days")
        ask = f"Book my trip to {dest}" + (f" for {days} days" if days else "") + ": show me flights, a hotel, a cab and activities there."
        return await self.travel.chat(s, ask)

    async def confirm_payment(self, link_id: str) -> tuple[str, list[dict]] | None:
        """Razorpay webhook: confirm the order, and if it is the user's current trip, empty it so the next one starts clean."""
        result = await self.travel.confirm_payment(link_id)
        if result:
            phone = "+" + result[0]
            convo = await self._db(self.repo.get_conversation, phone)
            ctx = dict((convo or {}).get("context") or {})
            if (ctx.get("trip_cart") or {}).get("link_id") == link_id:
                ctx.pop("trip_cart")
                await self._db(self.repo.save_conversation, phone, (convo or {}).get("current_step", "chat"), ctx)
        return result

    async def _remember(self, s: Session, text: str, out: list[dict]) -> None:
        """Best-effort chat history (the travel buddy reads it back); the bot keeps working without the `messages` table."""
        try:
            await self._db(self.repo.log_message, s.user["id"], "user", s.ctx.get("agent"), text or "(no text)")
            for m in out:
                if body := m.get("body"):
                    await self._db(self.repo.log_message, s.user["id"], "assistant", s.ctx.get("agent"), body)
        except Exception:
            logger.warning("Could not save chat history (is the `messages` table created?)", exc_info=False)
