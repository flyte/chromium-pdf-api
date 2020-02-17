import asyncio
import json
import logging
from contextlib import contextmanager
from random import randint

import aiohttp
import websockets

LOG = logging.getLogger(__name__)


class ReceiveLoopStopped(Exception):
    pass


class PayloadTooBig(Exception):
    pass


class NavigationError(Exception):
    def __init__(self, *args, url=None, code=None, **kwargs):
        self.url = url
        self.code = code
        super().__init__(*args, **kwargs)


class PageLoadTimeout(TimeoutError):
    pass


class StatusTimeout(TimeoutError):
    pass


class PDFPrintTimeout(TimeoutError):
    pass


async def chrome_ok(cdp_host):
    # Check that the JSON API works
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{cdp_host}/json") as resp:
            assert resp.status == 200, f"Chromium's JSON API returned {resp.status}"
            await resp.json()


class FrameRequestListener:
    def __init__(self, cdp, frame_id, loop=None):
        self.cdp = cdp
        self.frame_id = frame_id
        self.request_id = None
        self.response = None
        self.loop = loop or asyncio.get_event_loop()
        self.update_task = self.loop.create_task(self.get_frame_updates())

    async def get_frame_updates(self):
        async for msg in self.cdp.subscribe(
            ["Network.requestWillBeSent", "Network.responseReceived"]
        ):
            method = msg.get("method")
            if all(
                (
                    self.request_id is None,
                    method == "Network.requestWillBeSent",
                    msg["params"]["frameId"] == self.frame_id,
                )
            ):
                self.request_id = msg["params"]["requestId"]
            elif all(
                (
                    self.response is None,
                    method == "Network.responseReceived",
                    msg["params"]["requestId"] == self.request_id,
                )
            ):
                self.response = msg["params"]["response"]
                return self.response


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
        ftree_resp = await cdp.send("Page.getFrameTree")
        main_frame_id = ftree_resp["frameTree"]["frame"]["id"]

        # Start frame lifecycle listener
        req_listener = FrameRequestListener(cdp, main_frame_id, loop=loop)
        nav_resp = await cdp.send("Page.navigate", dict(url=url, frameId=main_frame_id))
        try:
            raise NavigationError(
                f'Main URL failed to load: {nav_resp["result"]["errorText"]}'
            )
        except KeyError:
            pass
        # async with cdp.subscribe(["Network.responseReceived"]) as msg:
        # Wait for the page to load
        try:
            await asyncio.wait_for(cdp.wait_for(loaded_event), timeout=load_timeout)
        except asyncio.TimeoutError:
            raise PageLoadTimeout()
        # Check if the main page had a successful status code
        try:
            response = await asyncio.wait_for(
                req_listener.update_task, timeout=status_timeout
            )
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


class CDPSession:
    def __init__(self, loop=None):
        self.ws = None
        self.ws_url = None
        self.connect_args = None
        self.loop = loop or asyncio.get_event_loop()
        self._used_cmd_ids = set()
        self.cmd_futures = {}
        self.method_queues = {}
        self._msg_rx_task = None
        self.listening_stopped = asyncio.Event(loop=self.loop)
        self.listening_cancelled = asyncio.Event(loop=self.loop)

    @property
    def _new_cmd_id(self):
        cid = None
        while cid is None or cid in self._used_cmd_ids:
            cid = randint(0, 1000000000)
        self._used_cmd_ids.add(cid)
        return cid

    @contextmanager
    def method_subscription(self, methods):
        queue = asyncio.Queue()
        try:
            for method in methods:
                self.add_method_queue(method, queue)
            yield queue
        finally:
            for method in methods:
                self.remove_method_queue(method, queue)

    async def connect(self, ws_url, **kwargs):
        self.ws_url = ws_url
        self.connect_args = kwargs
        self.ws = await websockets.connect(ws_url, **kwargs)
        self._msg_rx_task = self.loop.create_task(self._msg_rx_loop())

    async def _msg_rx_loop(self):
        rx = []
        try:
            while True:
                msg = await self.ws.recv()
                data = json.loads(msg)
                rx.append(data)
                cmd_id = data.get("id")
                method = data.get("method")
                try:
                    self.cmd_futures[cmd_id].set_result(data)
                except KeyError:
                    pass
                for queue in self.method_queues.get(method, []):
                    queue.put_nowait(data)
                for queue in self.method_queues.get("*", []):
                    queue.put_nowait(data)
        except asyncio.CancelledError:
            pass
        finally:
            self.listening_stopped.set()
            import yaml

            with open("/home/flyte/blah.yml", "w") as f:
                yaml.dump(rx, f)

    async def send(self, method, params=None, await_response=True):
        if params is None:
            params = {}
        cmd_id = self._new_cmd_id
        if await_response:
            response = self.loop.create_future()
            self.cmd_futures[cmd_id] = response
        try:
            await self.ws.send(json.dumps(dict(id=cmd_id, method=method, params=params)))
            if not await_response:
                return

            # Wait for the response Future to have its result set, or the listening
            # loop task to stop running for some reason.
            done, _ = await asyncio.wait(
                [response, self._msg_rx_task], return_when=asyncio.FIRST_COMPLETED
            )
            if response in done:
                return response.result()["result"]
            # If we get here, then the rx task has stopped for some reason
            exception = self._msg_rx_task.exception()
            if exception is not None:
                cause = exception.__cause__ or exception
                raise cause
            raise ReceiveLoopStopped("CDP websocket listening loop has stopped")
        finally:
            if await_response:
                del self.cmd_futures[cmd_id]

    def add_method_queue(self, method, queue):
        try:
            self.method_queues[method].add(queue)
        except KeyError:
            self.method_queues[method] = {queue}

    def remove_method_queue(self, method, queue):
        method_queues = self.method_queues[method]
        method_queues.remove(queue)
        if not method_queues:
            del self.method_queues[method]

    async def subscribe(self, methods):
        with self.method_subscription(methods) as queue:
            while not self.listening_stopped.is_set():
                yield await queue.get()
            raise ReceiveLoopStopped("CDP websocket listening loop has stopped")

    async def wait_for(self, method):
        with self.method_subscription([method]) as queue:
            return await queue.get()

    async def disconnect(self):
        self._msg_rx_task.cancel()
        # await self.listening_cancelled.wait()
        await self.ws.close()
