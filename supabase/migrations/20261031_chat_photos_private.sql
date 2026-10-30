-- Diogram: закрыть бакет chat-photos (вложения чатов).
-- ПРИМЕНЯТЬ ТОЛЬКО ПОСЛЕ выкатки кода, который отдаёт вложения через /api/f/... (PR «приватный бакет»),
-- иначе старые клиенты и сервер будут показывать битые картинки.
-- Сервер ходит под service_role и подписывает ссылки сам; иконка монеты отдаётся сервером с /assets/coin.jpg.
-- Откат: 20261031_chat_photos_private.rollback.sql
update storage.buckets set public = false where id = 'chat-photos';
