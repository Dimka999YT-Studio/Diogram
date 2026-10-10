import asyncio, websockets, os
from http import HTTPStatus

clients = set()

async def ws_handler(websocket):
    clients.add(websocket)
    try:
        async for message in websocket:
            for c in clients:
                if c != websocket:
                    await c.send(message)
    finally:
        clients.remove(websocket)

async def process_request(path, headers):
    if path in ("/", "/index.html"):
        with open("index.html", "rb") as f:
            body = f.read()
        return (HTTPStatus.OK, [("Content-Type", "text/html; charset=utf-8")], body)
    return (HTTPStatus.NOT_FOUND, [], b"Not found")

async def main():
    port = int(os.environ.get("PORT", 8765))
    async with websockets.serve(ws_handler, "0.0.0.0", port, process_request=process_request):
        print(f"Server on {port}")
        await asyncio.Future()

asyncio.run(main())
