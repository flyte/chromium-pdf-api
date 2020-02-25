import asyncio
import json
import logging
import logging.config
import zlib
from base64 import b64decode, b64encode
from os import environ as env
from uuid import uuid4

from aiohttp import web

from cdp import PayloadTooBig
from pdf import NavigationError, chrome_ok, get_pdf

CDP_HOST = env.get("CDP_HOST", "http://localhost:9222")

logging.config.dictConfig(
    dict(
        version=1,
        formatters=dict(
            standard=dict(format="%(asctime)s %(name)s (%(levelname)s): %(message)s")
        ),
        handlers=dict(
            console={
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "level": "DEBUG",
                "stream": "ext://sys.stdout",
            }
        ),
        loggers=dict(
            cdp=dict(
                level=getattr(logging, env.get("CDP_LOG_LEVEL", "DEBUG").upper()),
                handlers=["console"],
            ),
            pdf=dict(
                level=getattr(logging, env.get("PDF_LOG_LEVEL", "DEBUG").upper()),
                handlers=["console"],
            ),
            server=dict(
                level=getattr(logging, env.get("SERVER_LOG_LEVEL", "DEBUG").upper()),
                handlers=["console"],
            ),
        ),
    )
)


LOG = logging.getLogger(__name__)


def bad_request(msg, data=None):
    if data is None:
        data = {}
    return web.json_response(dict(error=msg, **data), status=400)


def gateway_timeout(msg, data=None):
    if data is None:
        data = {}
    return web.json_response(dict(error=msg, **data), status=504)


def payload_too_large(msg, data=None):
    if data is None:
        data = {}
    return web.json_response(dict(error=msg, **data), status=413)


def failed_dependency(msg, url, code, data=None):
    if data is None:
        data = {}
    return web.json_response(
        dict(error=msg, failed_url=url, status_code=code, **data), status=424
    )


async def pdf(request):
    trace = str(uuid4())
    try:
        data = await request.json()
    except json.decoder.JSONDecodeError:
        return bad_request("Must provide valid JSON")

    if "url" not in data:
        return bad_request("Must provide 'url'", data)
    timeout = int(data.pop("timeout", 120))
    compress = data.pop("compress", False)

    LOG.info(f"{trace} Generating PDF for url {data['url']}")

    try:
        pdf = await asyncio.wait_for(get_pdf(CDP_HOST, **data, trace=trace), timeout)
    except TimeoutError as e:
        return gateway_timeout(str(e), data)
    except PayloadTooBig as e:
        return payload_too_large(str(e), data)
    except NavigationError as e:
        url = e.url or data["url"]
        return failed_dependency(str(e), url, e.code)

    if compress:
        pdf = b64encode(zlib.compress(b64decode(pdf))).decode("utf8")

    LOG.info(f"{trace} PDF returned successfully")
    return web.json_response(dict(pdf=pdf, **data))


async def healthcheck(request):
    try:
        await chrome_ok(CDP_HOST)
    except Exception as e:
        return web.Response(text=e, status=500)
    return web.Response(text="OK")


if __name__ == "__main__":
    app = web.Application()
    app.add_routes([web.post("/", pdf), web.get("/healthcheck/", healthcheck)])
    web.run_app(app)
