#!/bin/bash

set -ex

# Run the browser
if [ ${RUN_BROWSER:-1} -eq 1 ]; then
    if [ ${SANDBOX:-0} -eq 1 ]; then
        chromium --headless --disable-gpu --remote-debugging-address=0.0.0.0 --remote-debugging-port=9222 &
    else
        chromium --headless --no-sandbox --disable-gpu --remote-debugging-address=0.0.0.0 --remote-debugging-port=9222 &
    fi
fi

exec "$@"
