import asyncio
import logging
from base64 import b64decode
from datetime import datetime, timedelta
from functools import partial
from subprocess import check_call

import aiohttp
from client import CDPSession, ReceiveLoopStopped

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.StreamHandler())
LOG.setLevel(logging.DEBUG)

CDP_HOST = "http://localhost:9222"


async def print_events(cdp):
    try:
        async for msg in cdp.subscribe(["*"]):
            print(msg)
    except ReceiveLoopStopped:
        pass


def handle_exception(cdp, loop, context):
    print("Exception handler called...")
    msg = context.get("exception", context["message"])
    print(msg)
    loop.create_task(disconnect(cdp))


async def run(cdp):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{CDP_HOST}/json/new") as resp:
            tab_info = await resp.json()
    tab_id = tab_info["id"]

    try:
        ws_url = tab_info["webSocketDebuggerUrl"]
        await cdp.connect(ws_url, close_timeout=2, max_size=20 * 1024 ** 2)

        # # Open the devtools
        # check_call(
        #     [
        #         "xdg-open",
        #         f"http://localhost:9222/devtools/inspector.html?ws=localhost:9222/devtools/page/{tab_id}",
        #     ]
        # )
        # # Wait for devtools to load
        # await asyncio.sleep(7)

        await cdp.send("Page.enable")
        await cdp.send("Network.enable")
        print("Navigating to URL...")
        await cdp.send("Page.navigate", dict(url="https://staging.welovemicro.com/"))
        print("Navigation started")

        loop = asyncio.get_event_loop()
        # Print all of the messages
        print_all_msgs = False
        if print_all_msgs:
            loop.create_task(print_events(cdp))

        # Use one or the other to establish that the page has loaded
        # loaded_event = "Page.domContentEventFired"
        loaded_event = "Page.loadEventFired"

        print("Awaiting page load...")
        await cdp.wait_for(loaded_event)

        print("Printing page...")
        try:
            resp = await cdp.send("Page.printToPDF")
        except Exception:
            LOG.exception("Exception during PDF creation:")
            return
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
    loop.set_exception_handler(partial(handle_exception, cdp))
    try:
        loop.run_until_complete(run(cdp))
    except KeyboardInterrupt:
        loop.run_until_complete(disconnect(cdp))
        loop.close()
