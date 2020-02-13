import asyncio
import json
from random import randint

import websockets


class Event:
    pass


class FrameLoadingEvent(Event):
    pass


class CDPSession:
    def __init__(self, loop=None):
        self.ws = None
        self.ws_url = None
        self.connect_args = None
        self.loop = loop or asyncio.get_event_loop()
        self._used_cmd_ids = set()
        self.cmd_futures = {}
        self.method_queues = {}

    @property
    def _new_cmd_id(self):
        cid = None
        while cid is None or cid in self._used_cmd_ids:
            cid = randint(0, 1000000000)
        self._used_cmd_ids.add(cid)
        return cid

    async def connect(self, ws_url, **kwargs):
        self.ws_url = ws_url
        self.connect_args = kwargs
        self.ws = await websockets.connect(ws_url, **kwargs)
        self._rx_task = self.loop.create_task(self._msg_rx_task())

    async def _msg_rx_task(self):
        async for msg in self.ws:
            data = json.loads(msg)
            cmd_id = data.get("id")
            method = data.get("method")
            try:
                self.cmd_futures[cmd_id].set_result(data)
            except KeyError:
                pass
            for queue in self.method_queues.get(method, []):
                queue.put_nowait(data)

    async def send(self, method, params=None, await_response=True):
        if params is None:
            params = {}
        cmd_id = self._new_cmd_id
        if await_response:
            response = self.loop.create_future()
            self.cmd_futures[cmd_id] = response
        try:
            await self.ws.send(json.dumps(dict(id=cmd_id, method=method, params=params)))
            if await_response:
                resp = await response
                return resp["result"]
        finally:
            if await_response:
                del self.cmd_futures[cmd_id]

    async def subscribe(self, methods):
        queue = asyncio.Queue()
        for method in methods:
            try:
                self.method_queues[method].add(queue)
            except KeyError:
                self.method_queues[method] = {queue}
        try:
            while True:
                yield await queue.get()
        finally:
            for method in methods:
                method_queues = self.method_queues[method]
                method_queues.remove(queue)
                if not method_queues:
                    del self.method_queues[method]
