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
}
```

## API

Everything except for the `url` parameter is optional.

```json
{
    "url": "<url of page to turn into PDF>",
    "max_size": "<maximum size (in bytes) of the PDF - will error if exceeded>",
    "timeout": "<maximum seconds to wait overall>",
    "load_timeout": "<maximum seconds to wait for the page to finish loading>",
    "status_timeout": "<maximum seconds to wait for the main HTTP request to return>",
    "print_timeout": "<maximum seconds to wait for the PDF to 'print'>",
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

## Healthcheck

You may HTTP GET the `/healthcheck/` endpoint to have the server perform a cursory healthcheck to ensure it can communicate with Chromium's DevTools API.

Returns a status code of `200` if communication is successful and `500` if not.

## Memory and concurrency

Chrom(e|ium) has a tendency to guzzle as much memory as it can get its hands on. You may find that this docker image crashes with an error along the lines of:

```
FATAL:memory.cc(22)] Out of memory. size=262144
```

In this case, you will need to increase the shared memory size using the `--shm-size=512M` command. The default is only `64M` so you may want to experiment with what size suits you, based on how many tabs you're likely to have open at once.

Another potential issue is how many tabs you really want to have open at once. This is by default limited to 10, but you can set this to whatever you like, using the `PDF_CONCURRENCY` environment variable:

```
docker run -ti --rm -p 8080:8080 -e PDF_CONCURRENCY=2 flyte/chromium-pdf-api
```

This can help to plan for the amount of memory your container is going to use, although it really depends how much memory the site you're PDFing uses as well.

## Cooperative Loading

Sometimes you'll want to make sure that any asynchronous content has completed loading before creating your PDF. You may do this by adding an HTML input element to the page with class `pdfloading`, then changing its value to `loaded` using JavaScript once your content is fully ready.

For example:

```html
<html>
    <body>
        <h1>My PDF</h1>
        <input type="hidden" id="loading1" class="pdfloading" value="loading" />
        <input type="hidden" id="loading2" class="pdfloading" value="loading" />
        <script>
            setTimeout(function() {
                document.getElementById("loading1").value = "loaded"
            }, 5000)
        </script>
        <script>
            setTimeout(function() {
                document.getElementById("loading2").value = "loaded"
            }, 8000)
        </script>
    </body>
</html>
```
