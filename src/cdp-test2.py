#!/usr/bin/env python
# coding: utf-8

import asyncio
import subprocess
import sys
from base64 import b64decode

from pdf import get_pdf

CDP_HOST = "http://localhost:9222"


loop = asyncio.get_event_loop()
res = loop.run_until_complete(get_pdf(CDP_HOST, sys.argv[1]))
with open("/tmp/pdf.pdf", "wb") as f:
    f.write(b64decode(res))
subprocess.check_call(["xdg-open", "/tmp/pdf.pdf"])
