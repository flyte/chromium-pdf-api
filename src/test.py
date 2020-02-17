import asyncio
from base64 import b64decode
from datetime import datetime, timedelta
from subprocess import check_call

import aiohttp
from client import CDPSession

CDP_HOST = "http://localhost:9222"


async def print_events(cdp):
    async for msg in cdp.subscribe(["*"]):
        print(msg)


async def run(cdp):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{CDP_HOST}/json/new") as resp:
            tab_info = await resp.json()
    tab_id = tab_info["id"]

    try:
        ws_url = tab_info["webSocketDebuggerUrl"]
        await cdp.connect(ws_url, close_timeout=2, max_size=50 * 1024 ** 2)

        # # Open the devtools
        # check_call(
        #     [
        #         "xdg-open",
        #         f"http://localhost:9222/devtools/inspector.html?ws=localhost:9222/devtools/page/{tab_id}",
        #     ]
        # )
        # # Wait for devtools to load
        # await asyncio.sleep(7)

        print(await cdp.send("Page.enable"))
        print(await cdp.send("Network.enable"))
        print(
            await cdp.send(
                "Page.navigate", dict(url="https://staging.welovemicro.com/document/7/")
            )
        )

        # # Print all of the messages
        # loop = asyncio.get_event_loop()
        # loop.create_task(print_events(cdp))

        # Use one or the other to establish that the page has loaded
        # loaded_event = "Page.domContentEventFired"
        loaded_event = "Page.loadEventFired"

        print("Awaiting load event...")
        await cdp.wait_for(loaded_event)

        print("Printing page...")
        resp = await cdp.send("Page.printToPDF", await_response=True)
        pdf_fname = f"{tab_id}.pdf"
        with open(pdf_fname, "wb") as f:
            f.write(b64decode(resp["data"]))
        check_call(["xdg-open", pdf_fname])
        print("Page printed!")
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
