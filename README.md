Chromium PDF API
================

A server which uses headless Chromium to visit any URL and create a PDF from it. Uses a simple JSON API to set the URL and options for PDF creation.

Usage
-----

Run the container:

```
docker run -ti --rm -p 8080:8080 flyte/chromium-pdf-api
```

Make a request:

```
curl -X POST \
    --header "Content-Type: application/json" \
    --data '{"url": "https://www.google.com"}' \
    localhost:8080
```

Example request:

```json
{
    "url": "https://www.google.com"
}
```

Example response:

```json
{
    "url": "https://www.google.com",
    "pdf": "<base64 string of PDF>",
    "load_timed_out": false
}
```

## API

Everything except for the `url` parameter is optional.

```json
{
    "url": "<url of page to turn into PDF>",
    "max_size": "<maximum size (in bytes) of the PDF - will error if exceeded>",
    "load_timeout": "<maximum seconds to wait for the page to finish loading>",
    "print_timeout": "<maximum seconds to wait for the PDF to 'print' - will error if exceeded>",
    "options": {
        # This is passed directly through to Chromium as options to the Page.printToPDF
        # function. You may omit this entirely, or use any of the options from this URL:
        # https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-printToPDF
        "landscape": true,
        "scale": 0.8,
        ... etc ...
    }
}
```

### Errors

PDF (or other CDP response) exceeded the `max_size` set

```json
{
    "url": "https://www.google.com",
    "max_size": "<whatever you set it to>",
    "error": "PDF exceeded maximum size"
}
```

Timeout waiting for Chromium to reply with the 'printed' PDF

```json
{
    "url": "https://www.google.com",
    "print_timeout": "<whatever you set it to>",
    "error": "Timeout printing PDF"
}
```