FROM python:3.7-alpine

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

WORKDIR /home/pdf

COPY requirements.txt ./

RUN apk add --no-cache --virtual .build-deps build-base && \
    pip install --no-cache-dir -r requirements.txt && \
    apk del .build-deps && \
    adduser -h /home/pdf -Ds /bin/sh pdf

USER pdf
COPY src src

EXPOSE 8080

CMD ["python", "src/server.py"]
