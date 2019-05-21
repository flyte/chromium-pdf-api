import json

from aiohttp import web
from pdf import get_pdf, PayloadTooBig

CDP_HOST = "http://localhost:9222"


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


async def pdf(request):
    try:
        data = await request.json()
    except json.decoder.JSONDecodeError:
        return bad_request("Must provide valid JSON")

    if "url" not in data:
        return bad_request("Must provide 'url'", data)

    try:
        pdf, load_timed_out = await get_pdf(CDP_HOST, **data)
    except TimeoutError as e:
        return gateway_timeout(str(e), data)
    except PayloadTooBig as e:
        return payload_too_large(str(e), data)

    return web.json_response(dict(pdf=pdf, load_timed_out=load_timed_out, **data))


if __name__ == "__main__":
    app = web.Application()
    app.add_routes([web.post("/", pdf)])
    web.run_app(app)
