# Supabase schema

Run the files in `migrations/` in order in the Supabase SQL editor (or `supabase db push`). They are idempotent.

The bot uses these tables: `users`, `conversations` (state per phone), `messages` (chat history the travel buddy reads
back), `orders` (options the user paid for) and `airports` (read at start-up so the reel planner recognises cities).

The older migrations also create tables for the removed flight/hotel/cab/visa/events/forex flows. They are left as-is so
the migration history matches your database; nothing in the app reads them any more.
