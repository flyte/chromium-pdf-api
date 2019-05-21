import json

from aiohttp import web
from pdf import get_pdf

CDP_HOST = "http://localhost:9222"


def bad_request(data, msg):
    return web.json_response(dict(error=msg, **data), status=400)


def gateway_timeout(data, msg):
    return web.json_response(dict(error=msg, **data), status=504)


async def pdf(request):
    try:
        data = await request.json()
    except json.decoder.JSONDecodeError:
        return bad_request(data, "Must provide valid JSON")

    try:
        url = data["url"]
    except KeyError:
        return bad_request(data, "Must provide 'url'")

    try:
        pdf = await get_pdf(CDP_HOST, **data)
    except TimeoutError as e:
        return gateway_timeout(data, str(e))

    return web.json_response(dict(pdf=pdf, **data))


if __name__ == "__main__":
    app = web.Application()
    app.add_routes([web.post("/", pdf)])
    web.run_app(app)
