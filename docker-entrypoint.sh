#!/bin/bash

set -ex

# Run the browser
chromium --headless --no-sandbox --disable-gpu --remote-debugging-port=9222 &

exec "$@"
