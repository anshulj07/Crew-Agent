-- Orders: the user's trip (one or more options the travel buddy showed: a flight, a hotel, a car...) paid with one
-- Razorpay payment link. Adding an item to the trip replaces the link: the old order becomes 'cancelled'. The webhook
-- marks an order 'paid' (even a replaced one, if the user paid its link anyway). Safe to re-run.
create table if not exists public.orders (
    id         uuid primary key default gen_random_uuid(),
    ref        text not null unique check (ref ~ '^CR[A-Z0-9]{6}$'),
    user_id    uuid not null references public.users (id),
    phone      text not null,
    items      jsonb not null default '[]'::jsonb,   -- [{kind, title, card, price, currency}]
    amount     numeric(12, 2) not null check (amount > 0),
    currency   text not null check (currency ~ '^[A-Z]{3}$'),
    status     text not null default 'created' check (status in ('created', 'paid', 'cancelled')),
    link_id    text unique,                          -- Razorpay payment link id (plink_...)
    short_url  text,
    paid_at    timestamptz,
    created_at timestamptz not null default now()
);
create index if not exists idx_orders_user on public.orders (user_id, created_at desc);
alter table public.orders enable row level security;
