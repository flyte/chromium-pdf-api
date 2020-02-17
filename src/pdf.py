import asyncio
import json
import logging
from datetime import datetime, timedelta
from os import environ as env
from random import randint
from time import time

import aiohttp
import websockets

LOG = logging.getLogger(__name__)

USED_IDS = set()

SEMAPHORE = asyncio.Semaphore(int(env.get("PDF_CONCURRENCY", 10)))


class PayloadTooBig(Exception):
    pass


class NavigationError(Exception):
    def __init__(self, *args, url=None, code=None, **kwargs):
        self.url = url
        self.code = code
        super().__init__(*args, **kwargs)


def random_id():
    rid = None
    while rid is None or rid in USED_IDS:
        rid = randint(0, 1000000000)
    USED_IDS.add(rid)
    return rid


async def send_ws_cmd(ws, id, method, params=None):
    if params is None:
        params = {}
    LOG.debug(
        "Sending WS command %r with ID %r and params %r",
        method,
        id,
        json.dumps(params, indent=2),
    )
    return await ws.send(json.dumps(dict(id=id, method=method, params=params)))


async def wait_for_cmd_response(ws, id, timeout_secs=30):
    LOG.debug("Awaiting response to command with ID %r", id)
    timeout = datetime.now() + timedelta(seconds=timeout_secs)
    while True:
        if datetime.now() > timeout:
            raise TimeoutError("Timeout waiting for response to command ID %s" % id)
        try:
            rx = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
            LOG.debug("Message received: %r", rx)
        except asyncio.TimeoutError:
            continue
        if rx.get("id") == id:
            return rx


async def wait_for_page_load(ws, navigate_cmd_id, main_request_id, timeout_secs=30):
    main_frame = None
    frames_loading = set()
    frames_complete = set()
    timeout = datetime.now() + timedelta(seconds=timeout_secs)
    while not frames_complete or frames_loading != frames_complete:
        if datetime.now() > timeout:
            raise TimeoutError("Timeout waiting for page to load")
        try:
            rx = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
            LOG.debug("Message received: %r", rx)
        except asyncio.TimeoutError:
            continue
        method = rx.get("method")
        if rx.get("id") == navigate_cmd_id:
            main_frame = rx["result"]["frameId"]
            try:
                raise NavigationError(
                    f'Main URL failed to load: {rx["result"]["errorText"]}'
                )
            except KeyError:
                pass
            frames_loading.add(main_frame)
        elif method == "Network.responseReceived":
            if rx["params"]["requestId"] != main_request_id:
                continue
            status_str = str(rx["params"]["response"]["status"])
            if status_str[0] in "45":  # 400 or 500 error
                raise NavigationError(
                    f"Main URL failed to load: HTTP status {status_str}",
                    url=rx["params"]["response"]["url"],
                    code=rx["params"]["response"]["status"],
                )
        elif method == "Page.frameStartedLoading":
            frames_loading.add(rx["params"]["frameId"])
        elif method == "Page.frameStoppedLoading":
            frame_id = rx["params"]["frameId"]
            frames_complete.add(frame_id)
            # IDEA: Possible implementations -@flyte at 20/05/2019, 22:53:43
            # Can probably just check the main frame, since it always seems
            # to finish loading last on the tests I've done.


async def print_pdf(ws, options, timeout_secs=10):
    cmd_id = random_id()
    await send_ws_cmd(ws, cmd_id, "Page.printToPDF", options)
    timeout = datetime.now() + timedelta(seconds=timeout_secs)
    while not datetime.now() > timeout:
        try:
            rx = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
            # LOG.debug("Message received: %r", rx)
        except asyncio.TimeoutError:
            continue
        if rx.get("id") == cmd_id:
            LOG.debug("Page.printToPDF response received")
            return rx["result"]["data"]
    raise TimeoutError("Timeout printing PDF")


async def chrome_ok(cdp_host):
    # Check that the JSON API works
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{cdp_host}/json") as resp:
            assert resp.status == 200, f"Chromium's JSON API returned {resp.status}"
            await resp.json()


async def get_pdf(
    cdp_host,
    url,
    options=None,
    max_size=20 * 1024 ** 2,
    load_timeout=30,
    print_timeout=10,
):
    """
    Create a new tab on a browser, navigate to the page, wait for it to load and capture
    a PDF. Closes tab afterwards. max_size is 20MB by default.
    """
    if options is None:
        options = {}
    max_size = int(max_size)
    load_timeout = float(load_timeout)
    print_timeout = float(print_timeout)

    # Wait for a semaphore to become available so we don't run more than n tasks
    # concurrently.
    async with SEMAPHORE:
        LOG.info(f"Getting PDF from {url} with options {options}")

        async with aiohttp.ClientSession() as session:
            # Open a new browser tab
            LOG.info("Opening new browser tab")
            async with session.get(f"{cdp_host}/json/new") as resp:
                tab_info = await resp.json()
            tab_id = tab_info["id"]
            LOG.info("New browser tab opened with ID %r", tab_id)
            try:
                ws_url = tab_info["webSocketDebuggerUrl"]
                # ping_interval=None because of
                # https://github.com/aaugustin/websockets/issues/618
                async with websockets.connect(
                    ws_url, max_size=max_size, ping_interval=None
                ) as ws:
                    cmd_id = random_id()
                    await send_ws_cmd(ws, cmd_id, "Page.enable")
                    await wait_for_cmd_response(ws, cmd_id, timeout_secs=4)
                    cmd_id = random_id()
                    await send_ws_cmd(ws, cmd_id, "Network.enable")
                    await wait_for_cmd_response(ws, cmd_id, timeout_secs=4)
                    cmd_id = random_id()
                    LOG.info("Navigating tab to %r", url)
                    page_loading = False
                    while not page_loading:
                        await send_ws_cmd(ws, cmd_id, "Page.navigate", dict(url=url))
                        try:
                            await wait_for_cmd_response(ws, cmd_id, timeout_secs=4)
                            page_loading = True
                            LOG.debug("Page loading has begun")
                        except TimeoutError:
                            LOG.debug("Resending navigate command")
                            continue
                    LOG.info("Waiting for page to load")
                    load_timed_out = False
                    try:
                        await wait_for_page_load(ws, cmd_id, load_timeout)
                    except TimeoutError:
                        load_timed_out = True
                    LOG.info("Printing PDF")
                    return await print_pdf(ws, options, print_timeout), load_timed_out
            except websockets.exceptions.ConnectionClosed as e:
                if isinstance(e.__cause__, websockets.exceptions.PayloadTooBig):
                    raise PayloadTooBig("PDF exceeded maximum size")
                raise
            finally:
                # Close the tab on the browser
                LOG.info("Closing browser tab %s", tab_id)
                async with session.get(f"{cdp_host}/json/close/{tab_id}") as resp:
                    await resp.text()
