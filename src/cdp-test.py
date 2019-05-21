#!/usr/bin/env python
# coding: utf-8

import asyncio
import base64
import json
import subprocess
import sys
from time import sleep

import aiohttp
import websockets

CDP_HOST = "http://localhost:9222"


async def get_pdf(uri, options=None):
    if options is None:
        options = {}
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{CDP_HOST}/json/new") as resp:
            page_info = await resp.json()
        page_id = page_info["id"]
        try:
            ws_uri = page_info["webSocketDebuggerUrl"]
            async with websockets.connect(ws_uri, max_size=2 ** 50) as ws:
                await ws.send(json.dumps(dict(id=0, method="Page.enable", params=dict())))
                await ws.send(
                    json.dumps(dict(id=1, method="Page.navigate", params=dict(url=uri)))
                )

                main_frame = None
                frames_loading = set()
                frames_complete = set()
                while not frames_complete or frames_loading != frames_complete:
                    rx = json.loads(await ws.recv())
                    method = rx.get("method")
                    if rx.get("id") == 1:
                        main_frame = rx["result"]["frameId"]
                        frames_loading.add(main_frame)
                    elif method == "Page.frameStartedLoading":
                        frames_loading.add(rx["params"]["frameId"])
                    elif method == "Page.frameStoppedLoading":
                        frame_id = rx["params"]["frameId"]
                        frames_complete.add(frame_id)
                        # IDEA: Possible implementations -@flyte at 20/05/2019, 22:53:43
                        # Can probably just check the main frame, since it always seems
                        # to finish loading last on the tests I've done.

                    print(
                        f"\rFrames loading: {len(frames_loading)} of {len(frames_complete)}",
                        end="",
                    )

                print("\nPrinting PDF...")
                await ws.send(
                    json.dumps(dict(id=2, method="Page.printToPDF", params=options))
                )
                while True:
                    rx = json.loads(await ws.recv())
                    if rx.get("id") == 2:
                        return rx
                    await asyncio.sleep(0.1)
        finally:
            async with session.get(f"{CDP_HOST}/json/close/{page_id}") as resp:
                await resp.text()


loop = asyncio.get_event_loop()
res = loop.run_until_complete(get_pdf(sys.argv[1]))
with open("/tmp/pdf.pdf", "wb") as f:
    f.write(base64.b64decode(res["result"]["data"]))
subprocess.check_call(["xdg-open", "/tmp/pdf.pdf"])
