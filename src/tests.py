import asyncio
from threading import Thread
from unittest.mock import Mock, patch

import pytest
from pdf import chrome_ok
from werkzeug.wrappers import Response


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
