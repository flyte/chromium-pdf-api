import asyncio
from datetime import datetime, timedelta

import aiohttp
from client import CDPSession

CDP_HOST = "http://localhost:9222"


async def run(cdp):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{CDP_HOST}/json/new") as resp:
            tab_info = await resp.json()
    tab_id = tab_info["id"]

    try:
        ws_url = tab_info["webSocketDebuggerUrl"]
        await cdp.connect(ws_url, close_timeout=2)
        print(await cdp.send("Page.enable"))
        print(await cdp.send("Network.enable"))
        print(await cdp.send("Page.navigate", dict(url="https://www.welovemicro.com/")))
        async for msg in cdp.subscribe(["*"]):
            print(msg)
    finally:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{CDP_HOST}/json/close/{tab_id}") as resp:
                await resp.text()


async def disconnect(cdp):
    other_tasks = [
        t for t in asyncio.Task.all_tasks() if t is not asyncio.Task.current_task()
    ]
    for t in other_tasks:
        t.cancel()
    cleanup = asyncio.gather(*other_tasks, return_exceptions=True)
    print("Waiting for tasks to quit...")
    try:
        await asyncio.wait_for(cleanup, timeout=5)
    except asyncio.TimeoutError:
        print("Tasks did not quit within timeout, so exiting anyway.")
    print("Disconnecting websocket...")
    await cdp.ws.close()


if __name__ == "__main__":
    cdp = CDPSession()
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(run(cdp))
    except KeyboardInterrupt:
        loop.run_until_complete(disconnect(cdp))
        loop.close()
