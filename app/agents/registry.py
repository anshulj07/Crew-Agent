"""Wiring: the OpenAI travel buddy plus the reel planner behind one Bot."""
import logging

from app.agents.bot import Bot
from app.agents.planner import OpenAIPlanner, PlannerAgent
from app.agents.travel import OpenAIBrain, TravelAgent
from app.core import places
from app.core.config import settings
from app.db.repository import CoreRepo
from app.services.razorpay_service import RazorpayService

logger = logging.getLogger(__name__)


def build_bot(supabase_client) -> Bot:
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY must be set in .env")
    repo = CoreRepo(supabase_client)
    try:  # the reel planner recognises the cities in the `airports` table
        places.load_airports(repo.list_airports())
    except Exception:
        logger.warning("Could not read airports; the reel planner will plan trips without offering to book them")
    travel = TravelAgent(repo, OpenAIBrain.create(settings.OPENAI_API_KEY, settings.OPENAI_MODEL, settings.OPENAI_GUARD_MODEL),
                         RazorpayService, settings.BUNDLE_DISCOUNT_PCT)
    planner = PlannerAgent(repo, OpenAIPlanner.create(settings.OPENAI_API_KEY, settings.OPENAI_MODEL, settings.OPENAI_TRANSCRIBE_MODEL))
    return Bot(repo, travel, planner)
