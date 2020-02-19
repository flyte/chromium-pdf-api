import asyncio
from threading import Thread
from time import sleep
from unittest.mock import Mock, patch

import pytest
import websockets
from pdf import chrome_ok
from werkzeug.wrappers import Response


async def hello(websocket, path):
    try:
        while True:
            name = await websocket.recv()
            greeting = f"Hello {name}"
            await websocket.send(greeting)
    except websockets.exceptions.ConnectionClosed:
        pass


@pytest.yield_fixture
def websocket_server():
    loop = asyncio.new_event_loop()
    server = websockets.serve(hello, "localhost", 8765, loop=loop)

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server)
        loop.run_forever()

    async def await_all():
        await asyncio.gather(
            *[
                t
                for t in asyncio.Task.all_tasks(loop=loop)
                if t != asyncio.Task.current_task(loop=loop)
            ],
            return_exceptions=True,
        )

    thread = Thread(target=run)
    thread.start()
    # Wait for the server to start listening
    while True:
        try:
            server.ws_server.sockets
            break
        except AttributeError:
            sleep(0.1)
    host, port = server.ws_server.sockets[0].getsockname()
    uri = f"ws://{host}:{port}"
    yield server.ws_server, uri
    loop.call_soon_threadsafe(server.ws_server.close())
    loop.stop()
    while loop.is_running():
        sleep(0.1)
    loop.run_until_complete(await_all())
    loop.close()
    thread.join()


@pytest.mark.asyncio
async def test_chrome_ok_fails_bad_host():
    cdp_host = "http://555.555.555.555"
    with pytest.raises(Exception):
        await chrome_ok(cdp_host)


@pytest.mark.asyncio
async def test_chrome_ok_fails_500(httpserver):
    httpserver.expect_request("/json").respond_with_response(Response(status=500))
    with pytest.raises(Exception):
        await chrome_ok(f"http://{httpserver.host}:{httpserver.port}")


@pytest.mark.asyncio
async def test_chrome_ok_fails_404(httpserver):
    httpserver.expect_request("/json").respond_with_response(Response(status=404))
    with pytest.raises(Exception):
        await chrome_ok(f"http://{httpserver.host}:{httpserver.port}")


@pytest.mark.asyncio
async def test_chrome_ok_fails_400(httpserver):
    httpserver.expect_request("/json").respond_with_response(Response(status=400))
    with pytest.raises(Exception):
        await chrome_ok(f"http://{httpserver.host}:{httpserver.port}")


@pytest.mark.asyncio
async def test_chrome_ok(httpserver):
    httpserver.expect_request("/json").respond_with_json(
        [
            {
                "description": "",
                "devtoolsFrontendUrl": "/devtools/inspector.html?ws=localhost:9222/devtools/page/CF7846732C294BF6028E6CA2E32607B7",
                "id": "CF7846732C294BF6028E6CA2E32607B7",
                "title": "about:blank",
                "type": "page",
                "url": "about:blank",
                "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/CF7846732C294BF6028E6CA2E32607B7",
            }
        ]
    )
    assert await chrome_ok(f"http://{httpserver.host}:{httpserver.port}") == None


@pytest.mark.asyncio
async def test_ws_hello(websocket_server):
    ws_server, uri = websocket_server
    assert ws_server.is_serving
    async with websockets.connect(uri) as ws:
        await ws.send("flyte")
        resp = await ws.recv()
    assert resp == "Hello flyte"
