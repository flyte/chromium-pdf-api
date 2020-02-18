import asyncio
import json
import logging
from os import environ as env

from aiohttp import web
from cdp import PayloadTooBig
from pdf import NavigationError, chrome_ok, get_pdf

CDP_HOST = "http://localhost:9222"

# Set the log level for this module
LOG = logging.getLogger(__name__)
LOG.addHandler(logging.StreamHandler())
LOG.setLevel(getattr(logging, env.get("SERVER_LOG_LEVEL", "INFO").upper()))

# Set the log level of the pdf module
_PDF_LOG = logging.getLogger("pdf")
_PDF_LOG.addHandler(logging.StreamHandler())
_PDF_LOG.setLevel(getattr(logging, env.get("PDF_LOG_LEVEL", "INFO").upper()))

# Set the log level of the cdp module
_CDP_LOG = logging.getLogger("cdp")
_CDP_LOG.addHandler(logging.StreamHandler())
_CDP_LOG.setLevel(getattr(logging, env.get("CDP_LOG_LEVEL", "INFO").upper()))


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
    try:
        data = await request.json()
    except json.decoder.JSONDecodeError:
        return bad_request("Must provide valid JSON")

    if "url" not in data:
        return bad_request("Must provide 'url'", data)
    timeout = int(data.pop("timeout", 120))

    LOG.info(f"Generating PDF for url {data['url']}")

    try:
        pdf = await asyncio.wait_for(get_pdf(CDP_HOST, **data), timeout)
    except TimeoutError as e:
        return gateway_timeout(str(e), data)
    except PayloadTooBig as e:
        return payload_too_large(str(e), data)
    except NavigationError as e:
        url = e.url or data["url"]
        return failed_dependency(str(e), url, e.code)

    LOG.info("PDF returned successfully")
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
