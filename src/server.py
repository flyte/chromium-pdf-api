import json

from aiohttp import web
from pdf import get_pdf


def bad_request(msg):
    return web.json_response(dict(error=msg), status=400)


async def pdf(request):
    try:
        data = await request.json()
    except json.decoder.JSONDecodeError:
        return bad_request("Must provide valid JSON")

    try:
        url = data["url"]
    except KeyError:
        return bad_request("Must provide 'url'")

    try:
        options = data["options"]
    except KeyError:
        options = {}

    return web.json_response(
        dict(
            url=url,
            options=options,
            pdf=await get_pdf("http://localhost:9222", url, options),
        )
    )


if __name__ == "__main__":
    app = web.Application()
    app.add_routes([web.post("/", pdf)])
    web.run_app(app)
