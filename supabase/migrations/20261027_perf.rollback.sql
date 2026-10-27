drop function if exists public.chat_summaries(bigint, bigint[]);
drop function if exists public.channel_summaries(bigint[]);
drop index if exists public.messages_unread_idx, public.chat_members_chat_user_idx, public.chat_members_user_idx, public.messages_client_id_unique;
alter table public.messages drop column if exists client_id;
