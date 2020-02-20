import asyncio
import json
from threading import Thread
from time import sleep
from unittest.mock import Mock, patch

import pytest
import websockets
from cdp import CDPSession, FrameRequestListener
from pdf import chrome_ok
from werkzeug.wrappers import Response


@pytest.yield_fixture
def websocket_server(request):
    loop = asyncio.new_event_loop()
    server = websockets.serve(
        globals()[f"proto_{request.function.__name__}"], "localhost", 8765, loop=loop
    )

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


async def proto_test_ws_hello(websocket, path):
    try:
        name = await websocket.recv()
        greeting = f"Hello {name}"
        await websocket.send(greeting)
    except websockets.ConnectionClosed:
        pass


@pytest.mark.asyncio
async def test_ws_hello(websocket_server):
    ws_server, uri = websocket_server
    assert ws_server.is_serving
    async with websockets.connect(uri) as ws:
        await ws.send("flyte")
        resp = await ws.recv()
    assert resp == "Hello flyte"


async def proto_test_frame_req_listener(websocket, path):
    try:
        navigate_cmd = json.loads(await websocket.recv())
        await websocket.send(json.dumps(dict(id=navigate_cmd["id"], result={})))
        await websocket.send(
            json.dumps(
                dict(
                    method="Network.requestWillBeSent",
                    params=dict(frameId="frameid1", requestId="reqid1"),
                )
            )
        )
        await websocket.send(
            json.dumps(
                dict(
                    method="Network.responseReceived",
                    params=dict(
                        requestId="reqid1",
                        response=dict(url=navigate_cmd["params"]["url"], status=200),
                    ),
                )
            )
        )
    except websockets.ConnectionClosed:
        pass


@pytest.mark.asyncio
async def test_frame_req_listener(websocket_server):
    """
    FrameRequestListener should pick up the status from the network request response.
    """
    ws_server, uri = websocket_server
    assert ws_server.is_serving
    cdp = CDPSession()
    await cdp.connect(uri)
    try:
        req_listener = FrameRequestListener(cdp, "frameid1")
        url = "http://www.example.com"
        await cdp.send("Page.navigate", dict(url=url))
        resp = await asyncio.wait_for(req_listener, timeout=1)
        assert resp["url"] == url
        assert resp["status"] == 200
    finally:
        await cdp.disconnect()


async def proto_test_cdpsession_method_subscription(websocket, path):
    try:
        await websocket.send(
            json.dumps(dict(method="Network.responseReceived", params=dict(rx="msg1")))
        )
        await websocket.send(
            json.dumps(dict(method="Network.somethingElse", params=dict(rx="msg2")))
        )
        await websocket.send(
            json.dumps(dict(method="IgnoreMe", params=dict(rx="ignored")))
        )
        await websocket.send(
            json.dumps(dict(method="Network.responseReceived", params=dict(rx="msg3")))
        )
    except websockets.ConnectionClosed:
        pass


@pytest.mark.asyncio
async def test_cdpsession_method_subscription(websocket_server):
    ws_server, uri = websocket_server
    assert ws_server.is_serving
    cdp = CDPSession()
    await cdp.connect(uri)
    try:
        with cdp.method_subscription(
            ["Network.responseReceived", "Network.somethingElse"]
        ) as queue:
            msg1 = await queue.get()
            assert msg1["method"] == "Network.responseReceived"
            assert msg1["params"]["rx"] == "msg1"
            msg2 = await queue.get()
            assert msg2["method"] == "Network.somethingElse"
            assert msg2["params"]["rx"] == "msg2"
            msg3 = await queue.get()
            assert msg3["method"] == "Network.responseReceived"
            assert msg3["params"]["rx"] == "msg3"

    finally:
        await cdp.disconnect()

