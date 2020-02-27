FROM alpine:3.11

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

RUN apk add --no-cache chromium && \
    adduser -h /home/chrome -Ds /bin/sh chrome

USER chrome
WORKDIR /home/chrome
EXPOSE 9222

ENTRYPOINT chromium-browser --headless --disable-gpu --disable-software-rasterizer --disable-dev-shm-usage --remote-debugging-address=0.0.0.0 --remote-debugging-port=9222
