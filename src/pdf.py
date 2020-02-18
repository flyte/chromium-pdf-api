import asyncio
import json
import logging
from contextlib import contextmanager
from random import randint

import aiohttp
import websockets

from cdp import CDPSession, FrameRequestListener

LOG = logging.getLogger(__name__)


class PageLoadTimeout(TimeoutError):
    pass


class StatusTimeout(TimeoutError):
    pass


class PDFPrintTimeout(TimeoutError):
    pass


class NavigationError(Exception):
    def __init__(self, *args, url=None, code=None, **kwargs):
        self.url = url
        self.code = code
        super().__init__(*args, **kwargs)


async def chrome_ok(cdp_host):
    # Check that the JSON API works
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{cdp_host}/json") as resp:
            assert resp.status == 200, f"Chromium's JSON API returned {resp.status}"
            await resp.json()
            LOG.debug("Chrome OK!")


async def get_pdf(
    cdp_host,
    url,
    options=None,
    max_size=20 * 1024 ** 2,
    load_timeout=30,
    status_timeout=5,
    print_timeout=10,
    loaded_event="Page.loadEventFired",
    loop=None,
):
    if options is None:
        options = {}
    if loop is None:
        loop = asyncio.get_event_loop()
    cdp = CDPSession(loop=loop)
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{cdp_host}/json/new") as resp:
            tab_info = await resp.json()
    tab_id = tab_info["id"]

    try:
        ws_url = tab_info["webSocketDebuggerUrl"]
        await cdp.connect(ws_url, close_timeout=2, max_size=max_size, ping_interval=None)
        await cdp.send("Page.enable")
        await cdp.send("Network.enable")

        # Get the tab's main frame ID
        ftree_resp = await cdp.send("Page.getFrameTree")
        main_frame_id = ftree_resp["frameTree"]["frame"]["id"]

        # Start frame request listener before we send the Page.navigate command since
        # the Network.requestWillBeSent message comes from the browser before the
        # response to Page.navigate does.
        req_listener = FrameRequestListener(cdp, main_frame_id, loop=loop)

        # Tell the tab to navigate to the desired URL
        nav_resp = await cdp.send("Page.navigate", dict(url=url, frameId=main_frame_id))
        try:
            raise NavigationError(
                f'Main URL failed to load: {nav_resp["result"]["errorText"]}'
            )
        except KeyError:
            pass

        # Wait for the page to load
        try:
            await asyncio.wait_for(cdp.wait_for(loaded_event), timeout=load_timeout)
        except asyncio.TimeoutError:
            raise PageLoadTimeout()

        # Check if the main page had a successful status code (the task should have
        # already completed long before the page finished loading.)
        try:
            response = await asyncio.wait_for(req_listener, timeout=status_timeout)
        except asyncio.TimeoutError:
            raise StatusTimeout()
        if str(response["status"])[0] != "2":
            raise NavigationError(
                f"Main URL failed to load: HTTP status {response['status']}",
                url=response["url"],
                code=response["status"],
            )
        try:
            pdf_resp = await asyncio.wait_for(
                cdp.send("Page.printToPDF", options), timeout=print_timeout
            )
        except asyncio.TimeoutError:
            raise PDFPrintTimeout()
        await cdp.send("Page.getFrameTree")
        return pdf_resp["data"]

    finally:
        await cdp.disconnect()
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{cdp_host}/json/close/{tab_id}") as resp:
                await resp.text()

