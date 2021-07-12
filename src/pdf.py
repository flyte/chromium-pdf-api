import asyncio
from asyncio.queues import QueueEmpty
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from os import environ as env
from random import randint

import aiohttp
import websockets

from cdp import CDPSession, FrameRequestListener

SEMAPHORE = asyncio.Semaphore(int(env.get("PDF_CONCURRENCY", 10)))
LOG = logging.getLogger(__name__)


def log(trace, level, msg, *args, **kwargs):
    if trace is not None:
        msg = f"{trace} {msg}"
    LOG.log(level, msg, *args, **kwargs)


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
    trace=None,
):
    if options is None:
        options = {}
    if loop is None:
        loop = asyncio.get_event_loop()
    max_size = int(max_size)
    load_timeout = int(load_timeout)
    status_timeout = int(status_timeout)
    print_timeout = int(print_timeout)

    async with SEMAPHORE:
        cdp = CDPSession(loop=loop, trace=trace)
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{cdp_host}/json/new") as resp:
                tab_info = await resp.json()
        tab_id = tab_info["id"]

        try:
            ws_url = tab_info["webSocketDebuggerUrl"]
            await cdp.connect(
                ws_url, close_timeout=2, max_size=max_size, ping_interval=None
            )
            await cdp.send("Page.enable")
            await cdp.send("Network.enable")

            # Get the tab's main frame ID
            ftree_resp = await cdp.send("Page.getFrameTree")
            main_frame_id = ftree_resp["frameTree"]["frame"]["id"]
            log(trace, logging.DEBUG, "Tab %s main frame ID is %s", tab_id, main_frame_id)

            # Start frame request listener before we send the Page.navigate command since
            # the Network.requestWillBeSent message comes from the browser before the
            # response to Page.navigate does.
            req_listener = FrameRequestListener(
                cdp, main_frame_id, loop=loop, trace=trace
            )

            # Tell the tab to navigate to the desired URL
            log(
                trace,
                logging.DEBUG,
                "Navigating tab %s frame %s to url %s",
                tab_id,
                main_frame_id,
                url,
            )
            with cdp.method_subscription(["DOM.attributeModified"]) as attrib_mod_queue:
                nav_resp = await cdp.send(
                    "Page.navigate", dict(url=url, frameId=main_frame_id)
                )
                try:
                    raise NavigationError(
                        f'Main URL failed to load: {nav_resp["result"]["errorText"]}'
                    )
                except KeyError:
                    pass
                log(trace, logging.DEBUG, "Navigation response received")

                # Wait for the page to load
                log(
                    trace,
                    logging.DEBUG,
                    "Awaiting page load using event %s for %s seconds",
                    loaded_event,
                    load_timeout,
                )
                try:
                    await asyncio.wait_for(
                        cdp.wait_for(loaded_event), timeout=load_timeout
                    )
                except asyncio.TimeoutError:
                    log(trace, logging.DEBUG, "Page load timed out")
                    raise PageLoadTimeout()
                log(trace, logging.DEBUG, "Page finished loading")

                # Check if the main page had a successful status code. The task should have
                # already completed long before the page finished loading.
                log(
                    trace,
                    logging.DEBUG,
                    "Awaiting main request status for %s seconds",
                    status_timeout,
                )
                try:
                    response = await asyncio.wait_for(
                        req_listener, timeout=status_timeout
                    )
                except asyncio.TimeoutError:
                    log(trace, logging.DEBUG, "Timeout waiting for status to be received")
                    raise StatusTimeout()
                log(
                    trace,
                    logging.DEBUG,
                    "Main request status received (%s)",
                    response["status"],
                )

                status = str(response["status"])
                if not status.startswith("2") and status != "304":
                    raise NavigationError(
                        f"Main URL failed to load: HTTP status {response['status']}",
                        url=response["url"],
                        code=response["status"],
                    )

                # TODO: Tasks pending completion -@flyte at 12/07/2021, 11:10:33
                # Reduce the timeout by how long we've already waited for loading.
                try:
                    doc_resp = await asyncio.wait_for(
                        cdp.send("DOM.getDocument"),
                        timeout=load_timeout,
                    )
                except asyncio.TimeoutError:
                    log(
                        trace,
                        logging.DEBUG,
                        "Timeout waiting for document to be returned",
                    )
                    raise PageLoadTimeout()
                log(trace, logging.DEBUG, "Document returned")

                print(doc_resp)

                log(trace, logging.DEBUG, "Checking for cooperative loading delay")
                try:
                    query_resp = await asyncio.wait_for(
                        cdp.send(
                            "DOM.querySelectorAll",
                            dict(
                                nodeId=doc_resp["root"]["nodeId"],
                                selector="input.pdfloading[value='loading']",
                            ),
                        ),
                        timeout=load_timeout,
                    )
                except asyncio.TimeoutError:
                    log(
                        trace,
                        logging.DEBUG,
                        "Timeout waiting for querySelector to be returned",
                    )
                    raise PageLoadTimeout()

                loading_elements = set(query_resp["nodeIds"])
                if loading_elements:
                    log(
                        trace,
                        logging.DEBUG,
                        "Waiting for cooperative loading to complete",
                    )
                    while loading_elements and (
                        not cdp.listening_stopped.is_set() or not attrib_mod_queue.empty()
                    ):
                        try:
                            modification = await asyncio.wait_for(
                                attrib_mod_queue.get(), timeout=load_timeout
                            )
                        except TimeoutError:
                            log(
                                trace,
                                logging.DEBUG,
                                "Waiting for DOM.attributeModified event timed out",
                            )
                            raise PageLoadTimeout()

                        node_id = modification["params"]["nodeId"]
                        if node_id not in loading_elements:
                            log(
                                trace,
                                logging.DEBUG,
                                "Modification was not on a loading element",
                            )
                            continue
                        if modification["params"]["name"] != "value":
                            log(
                                trace,
                                logging.DEBUG,
                                "Modification was not of the 'value' attribute",
                            )
                            continue
                        if modification["params"]["value"] != "loaded":
                            log(
                                trace,
                                logging.DEBUG,
                                "Modification value was not set to 'loaded'",
                            )
                            continue
                        loading_elements.remove(node_id)
                        if not loading_elements:
                            log(trace, logging.DEBUG, "Cooperative loading complete")
                else:
                    log(trace, logging.DEBUG, "No cooperative loading used")

            log(
                trace,
                logging.DEBUG,
                "Awaiting PDF print for %s seconds using the following options: %s",
                print_timeout,
                options,
            )
            try:
                pdf_resp = await asyncio.wait_for(
                    cdp.send("Page.printToPDF", options), timeout=print_timeout
                )
            except asyncio.TimeoutError:
                raise PDFPrintTimeout()
            # await cdp.send("Page.getFrameTree")
            return pdf_resp["data"]

        finally:
            await cdp.disconnect()
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{cdp_host}/json/close/{tab_id}") as resp:
                    await resp.text()
                    log(trace, logging.DEBUG, "Tab %s closed", tab_id)
