FROM python:3.7-alpine

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

RUN apk add --no-cache build-base && \
    pip install pipenv && \
    adduser -h /home/pdf -Ds /bin/sh pdf

USER pdf
WORKDIR /home/pdf

COPY Pipfile* ./
RUN pipenv install --deploy

COPY src src

EXPOSE 8080

CMD ["pipenv", "run", "python", "src/server.py"]
