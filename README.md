# WhatsApp Travel Buddy

A WhatsApp bot (Meta Cloud API + FastAPI + Supabase) that finds the best deals on flights, hotels, cars and activities
near the user. Every reply comes from OpenAI. The only thing done in code is **payment**: options the model shows get a
*Book* button, which sends a Razorpay payment link. A separate **reel planner** turns a shared travel reel into a trip plan.

## How a message flows
```
WhatsApp ─▶ api/routes/whatsapp.py ─▶ Bot (agents/bot.py)
                                        ├─ reel link / video / screenshot / planner buttons ─▶ agents/planner/
                                        └─ everything else ─▶ agents/travel/ ─▶ OpenAI ─▶ reply + Book buttons
```
- **Two OpenAI calls per message, at the same time** (`travel/brain.py`): a strict topic guard (travel only, else a fixed
  refusal from code) and the answer. Strict JSON schema output: `{on_topic, purpose, reply, options: [{kind, title, card, price, currency}]}`.
- **Few steps:** the model assumes defaults and asks for anything missing in ONE message with its guesses pre-filled.
- **Everything in USD.** Options priced in any other currency are dropped in code.
- **Each deal is a card** with a *Book · $price* button. The first time flights or hotels are shown, the bot asks
  *Vacation / Business* with two buttons and tailors the rest of the trip to it.
- **Trip cart, one payment:** Book adds the deal to the user's trip and sends ONE Razorpay link for the whole trip. Adding
  more replaces the link with a new total (the old link is cancelled). `BUNDLE_DISCOUNT_PCT` takes that % off a trip
  with two or more kinds (flight + hotel...).
- **Selling:** after every Book tap the model shows the next thing the trip needs (flight → hotel → car → activities),
  and reminds the user of things they looked at but didn't add.
- **Webhook** (`payment_link.paid`): marks the order paid, sends the confirmation on WhatsApp, empties the trip. Your
  team then makes the actual bookings from the `orders` table (items, total, ref).
- **Prices are the model's estimates**, not live inventory; the bot says so with every set of options.
- **Memory:** the last 12 messages from the `messages` table, the user's shared location pin (3 hours) and their trip.
- **Reel planner → booking:** "Book this trip" in the planner hands the destination to the travel buddy.

## Layout
```
app/
  main.py                    FastAPI app
  api/routes/whatsapp.py     webhook (verify + receive)
  api/routes/razorpay.py     payment webhook
  agents/bot.py              routes each message to the planner or the travel buddy; state + chat history
  agents/travel/             brain.py (prompt + OpenAI call), agent.py (reply, Book buttons, payment)
  agents/planner/            reel -> trip plan (OpenAI vision + Whisper, ffmpeg)
  core/  db/  services/      helpers, Supabase access, WhatsApp + Razorpay clients
supabase/migrations/         SQL; 20261014000000_orders.sql adds the `orders` table
```

## Run
```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000     # then expose it, e.g. ngrok http 8000
```
Required `.env` keys are in `.env.example`. Run `supabase/migrations/20261014000000_orders.sql` in the Supabase SQL editor once.

**Webhook security:** `POST /webhook/whatsapp` only accepts calls signed by Meta (`X-Hub-Signature-256`, checked against
`WHATSAPP_APP_SECRET`). The Razorpay webhook is verified the same way with `RAZORPAY_WEBHOOK_SECRET`.

**Razorpay:** dashboard → Settings → Webhooks → URL `https://<your-host>/webhook/razorpay`, event **Payment Link → paid**.
Prices in currencies other than INR need international payments enabled on your Razorpay account.

**Privacy:** messages, shared locations and reel videos/frames are sent to OpenAI. Say so in your privacy text.
