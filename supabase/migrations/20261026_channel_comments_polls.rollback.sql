drop table if exists public.channel_poll_votes;
alter table public.channel_messages drop column if exists poll, drop column if exists discussion_message_id;
drop index if exists public.messages_reply_to_idx;
alter table public.messages drop column if exists reply_to_message_id, drop column if exists channel_post_id;
drop index if exists public.channels_linked_chat_unique;
alter table public.channels drop column if exists linked_chat_id;
