"""Links to Expedia's public search pages for an option the travel buddy shows. Built here from the details the model gives
(place, city, airport codes, dates, travellers), never written by the model, so a link can't be invented. These are search
pages, not a single product page: Expedia's product ids aren't public, so a hotel link searches for that hotel by name."""
import re
from datetime import date
from urllib.parse import quote, urlencode

EXPEDIA = "https://www.expedia.com"
IATA = re.compile(r"[A-Z]{3}")


def _day(text: str) -> date | None:
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        return None


def _us(d: date) -> str:
    return d.strftime("%m/%d/%Y")


def link(kind: str, spec: dict | None) -> str:
    """The Expedia search link for one option, or "" when there isn't enough to build a useful one."""
    spec = spec or {}
    place, city = (spec.get("place") or "").strip(), (spec.get("city") or "").strip()
    start, end = _day(spec.get("start")), _day(spec.get("end"))
    if end and start and end <= start:
        end = None
    adults = max(1, min(int(spec.get("travellers") or 1), 9))

    if kind == "hotel" and (place or city):
        q = {"destination": f"{place}, {city}".strip(", "), "adults": adults, "rooms": 1}
        if start and end:
            q |= {"startDate": start.isoformat(), "endDate": end.isoformat()}
        return f"{EXPEDIA}/Hotel-Search?{urlencode(q)}"

    if kind == "flight":
        frm, to = (spec.get("from") or "").strip().upper(), (spec.get("to") or "").strip().upper()
        if not (IATA.fullmatch(frm) and IATA.fullmatch(to) and frm != to and start):
            return ""
        legs = f"leg1=from:{frm},to:{to},departure:{_us(start)}TANYT"
        if end:
            legs += f"&leg2=from:{to},to:{frm},departure:{_us(end)}TANYT"
        trip = "roundtrip" if end else "oneway"
        return (f"{EXPEDIA}/Flights-Search?trip={trip}&{legs}"
                f"&passengers=adults:{adults},children:0,seniors:0,infantinlap:Y&options=cabinclass:economy&mode=search")

    if kind == "car" and (place or city):
        q = {"locn": place or city}
        if start and end:
            q |= {"date1": _us(start), "date2": _us(end)}
        return f"{EXPEDIA}/carsearch?{urlencode(q, quote_via=lambda v, *_: quote(v, safe='/'))}"

    if kind in ("activity", "transfer") and city:
        q = {"location": city}
        if start:
            q |= {"startDate": start.isoformat(), "endDate": (end or start).isoformat()}
        return f"{EXPEDIA}/things-to-do/search?{urlencode(q)}"

    if kind == "cruise":
        return f"{EXPEDIA}/Cruises"
    return ""
