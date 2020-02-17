import asyncio
import json
import logging
from contextlib import contextmanager
from random import randint

import websockets


LOG = logging.getLogger(__name__)


class ReceiveLoopStopped(Exception):
    pass


@contextmanager
def method_subscription(cdp, methods):
    queue = asyncio.Queue()
    try:
        for method in methods:
            cdp.add_method_queue(method, queue)
        yield queue
    finally:
        for method in methods:
            cdp.remove_method_queue(method, queue)


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
        try:
            while not self.listening_cancelled.is_set():
                try:
                    msg = await asyncio.wait_for(self.ws.recv(), timeout=1)
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    LOG.exception("Exception during websocket recv:")
                    raise
                data = json.loads(msg)
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
        finally:
            self.listening_stopped.set()

    async def send(self, method, params=None, await_response=True, response_timeout=10):
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
            # Wait for a response using the configured timeout
            done, _ = await asyncio.wait_for(
                # Wait for the response Future to have its result set, or the listening
                # loop task to finish running for some reason.
                asyncio.wait(
                    [response, self._msg_rx_task], return_when=asyncio.FIRST_COMPLETED
                ),
                timeout=response_timeout,
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
