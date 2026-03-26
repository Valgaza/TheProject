import asyncio
import httpx
import websockets

async def main():
    # Step 1: send POST
    resp = httpx.post(
        "http://localhost:8000/ingest",
        json={
            "message": "Alice works at Google",
            "user_id": "user_1",
            "project_id": "proj_1"
        }
    )
    print("POST:", resp.status_code, resp.text)

    # Step 2: listen for updates
    async with websockets.connect("ws://localhost:8000/ws/user_1") as ws:
        while True:
            msg = await ws.recv()
            print("WS:", msg)

asyncio.run(main())