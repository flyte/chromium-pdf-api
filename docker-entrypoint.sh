#!/bin/bash

set -ex

# Run the browser
chromium --headless --no-sandbox --disable-setuid-sandbox --disable-gpu --remote-debugging-address=0.0.0.0 --remote-debugging-port=9222 &

exec "$@"
