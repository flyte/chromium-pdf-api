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


class FrameRequestListener:
    """
    Listens for state updates to a frame and stores the response sent by the server.

    Should be instantiated before the Page.navigate command is sent so that it can pick
    up the Network.requestWillBeSent message. Will then listen for the relevant
    Network.responseReceived message and store the given response. Will ignore any
    intermediary redirects (via Network.responseReceivedExtraInfo).
    """

    def __init__(self, cdp, frame_id, loop=None):
        self._cdp = cdp
        self._frame_id = frame_id
        self._loop = loop or asyncio.get_event_loop()
        self._request_id = None
        self._response = None
        self._update_task = self._loop.create_task(self._get_frame_updates())

    @property
    def response(self):
        return self._response

    async def __await__(self):
        """
        Allows the calling code to just await the class instance itself to receive the
        server's response.

        ```
        req_listener = FrameRequestListener(cdp, frame_id)
        # ... Send Page.navigate and wait for the page to load ...
        response = await req_listener
        ```
        """
        return await self._update_task

    async def _get_frame_updates(self):
        """
        Subscribe to the relevant methods on the CDP client and update our state
        according to the messages received. Will store the request's response for access
        at .response but also return it, since implementing code may want to await this
        task's completion.
        """
        async for msg in self._cdp.subscribe(
            ["Network.requestWillBeSent", "Network.responseReceived"]
        ):
            method = msg.get("method")
            if (
                self._request_id is None
                and method == "Network.requestWillBeSent"
                and msg["params"]["frameId"] == self._frame_id
            ):
                self._request_id = msg["params"]["requestId"]
                LOG.debug(
                    "FrameId %s has a requestId of %s", self._frame_id, self._request_id
                )
            elif (
                self._response is None
                and method == "Network.responseReceived"
                and msg["params"]["requestId"] == self._request_id
            ):
                self._response = msg["params"]["response"]
                LOG.debug(
                    "FrameId %s has received a response of %s for url %s",
                    self._frame_id,
                    self._response["status"],
                    self._response["url"],
                )
                return self._response


class CDPSession:
    def __init__(self, loop=None):
        self._loop = loop or asyncio.get_event_loop()
        self.listening_cancelled = asyncio.Event(loop=self._loop)
        self.listening_stopped = asyncio.Event(loop=self._loop)
        self._cmd_futures = {}
        self._method_queues = {}
        self._msg_rx_task = None
        self._used_cmd_ids = set()
        self._ws = None

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
        self._ws = await websockets.connect(ws_url, **kwargs)
        self._msg_rx_task = self._loop.create_task(self._msg_rx_loop())

    async def _msg_rx_loop(self):
        try:
            while True:
                msg = await self._ws.recv()
                data = json.loads(msg)
                cmd_id = data.get("id")
                method = data.get("method")
                try:
                    self._cmd_futures[cmd_id].set_result(data)
                except KeyError:
                    pass
                for queue in self._method_queues.get(method, []):
                    queue.put_nowait(data)
                for queue in self._method_queues.get("*", []):
                    queue.put_nowait(data)
        except (asyncio.CancelledError, websockets.ConnectionClosed):
            pass
        finally:
            self.listening_stopped.set()

    async def send(self, method, params=None, await_response=True):
        if params is None:
            params = {}
        cmd_id = self._new_cmd_id
        if await_response:
            response = self._loop.create_future()
            self._cmd_futures[cmd_id] = response
        try:
            await self._ws.send(json.dumps(dict(id=cmd_id, method=method, params=params)))
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
                del self._cmd_futures[cmd_id]

    def add_method_queue(self, method, queue):
        try:
            self._method_queues[method].add(queue)
        except KeyError:
            self._method_queues[method] = {queue}

    def remove_method_queue(self, method, queue):
        method_queues = self._method_queues[method]
        method_queues.remove(queue)
        if not method_queues:
            del self._method_queues[method]

    async def subscribe(self, methods):
        with self.method_subscription(methods) as queue:
            while not self.listening_stopped.is_set():
                try:
                    yield await asyncio.wait_for(queue.get(), 1)
                except asyncio.TimeoutError:
                    continue
            raise ReceiveLoopStopped("CDP websocket listening loop has stopped")

    async def wait_for(self, method):
        with self.method_subscription([method]) as queue:
            return await queue.get()

    async def disconnect(self):
        self._msg_rx_task.cancel()
        await self._ws.close()
