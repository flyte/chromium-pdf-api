import asyncio
import json
from datetime import datetime, timedelta
from threading import Thread
from time import sleep
from unittest.mock import Mock, patch

import pytest
import websockets
from cdp import CDPSession, FrameRequestListener
from pdf import chrome_ok
from werkzeug.wrappers import Response


@pytest.yield_fixture
def websocket_server(request, unused_tcp_port):
    loop = asyncio.new_event_loop()
    server = websockets.serve(
        globals()[f"proto_{request.function.__name__}"],
        "localhost",
        unused_tcp_port,
        loop=loop,
        close_timeout=2,
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

    async def cancel_all():
        for t in asyncio.Task.all_tasks(loop=loop):
            if t != asyncio.Task.current_task(loop=loop):
                t.cancel()

    def tasks_running():
        return not all(t.done() for t in asyncio.Task.all_tasks(loop=loop))

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

    # Wait for the ws_server to stop
    timeout = datetime.now() + timedelta(seconds=5)
    while datetime.now() < timeout and tasks_running():
        sleep(0.1)
    if tasks_running():
        asyncio.run_coroutine_threadsafe(cancel_all(), loop=loop)
        await_task = asyncio.run_coroutine_threadsafe(await_all(), loop=loop)
        try:
            await_task.result(timeout=5)
        except asyncio.CancelledError:
            pass

    loop.call_soon_threadsafe(loop.stop)
    thread.join()
    loop.close()


@pytest.mark.asyncio
@pytest.yield_fixture
async def cdp(websocket_server):
    ws_server, uri = websocket_server
    assert ws_server.is_serving
    cdp = CDPSession()
    await cdp.connect(uri)
    try:
        yield cdp
    finally:
        await cdp.disconnect()


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
async def test_frame_req_listener(cdp):
    """
    FrameRequestListener should pick up the status from the network request response.
    """
    req_listener = FrameRequestListener(cdp, "frameid1")
    url = "http://www.example.com"
    await cdp.send("Page.navigate", dict(url=url))
    resp = await asyncio.wait_for(req_listener, timeout=1)
    assert resp["url"] == url
    assert resp["status"] == 200


async def proto_test_cdpsession_method_subscription(websocket, path):
    try:
        # Wait for the client to say it's ready
        await websocket.recv()
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
async def test_cdpsession_method_subscription(cdp):
    with cdp.method_subscription(
        ["Network.responseReceived", "Network.somethingElse"]
    ) as queue:
        # Tell the server we're ready
        await cdp.send("", await_response=False)
        msg1 = await queue.get()
        assert msg1["method"] == "Network.responseReceived"
        assert msg1["params"]["rx"] == "msg1"
        msg2 = await queue.get()
        assert msg2["method"] == "Network.somethingElse"
        assert msg2["params"]["rx"] == "msg2"
        msg3 = await queue.get()
        assert msg3["method"] == "Network.responseReceived"
        assert msg3["params"]["rx"] == "msg3"


def test_cdpsession_method_subscription_removes_queues():
    """
    Subscription should be removed from the _method_queues set after ctx mgr exits.
    """
    cdp = CDPSession()
    with cdp.method_subscription(["Something"]) as queue:
        assert "Something" in cdp._method_queues
        assert queue in cdp._method_queues["Something"]
    assert "Something" not in cdp._method_queues


async def proto_test_cdpsession_non_json_response(websocket, path):
    # Wait for client to say they're ready
    await websocket.recv()
    await websocket.send("non json stuff")
    await websocket.send(json.dumps(dict(method="finished")))


@pytest.mark.asyncio
async def test_cdpsession_non_json_response(cdp):
    # All we're checking for here is that the CDP rx loop doesn't stop when it receives
    # a non-json message.
    with cdp.method_subscription(["finished"]) as queue:
        await cdp.send("", await_response=False)
        try:
            await asyncio.wait_for(queue.get(), timeout=5)
        except asyncio.TimeoutError:
            pass
        assert not cdp.listening_stopped.is_set()


async def proto_test_cdpsession_list_response(websocket, path):
    # Wait for client to say they're ready
    await websocket.recv()
    await websocket.send(json.dumps([]))
    await websocket.send(json.dumps(dict(method="finished")))


@pytest.mark.asyncio
async def test_cdpsession_list_response(cdp):
    # All we're checking for here is that the CDP rx loop doesn't stop when it receives
    # a json list message instead of a dict.
    with cdp.method_subscription(["finished"]) as queue:
        await cdp.send("", await_response=False)
        try:
            await asyncio.wait_for(queue.get(), timeout=5)
        except asyncio.TimeoutError:
            pass
        assert not cdp.listening_stopped.is_set()
