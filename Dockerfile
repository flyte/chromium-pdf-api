FROM python:3.6-buster

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y chromium && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install pipenv

RUN ["useradd", "-d", "/home/pdf", "-m", "-s", "/bin/bash", "pdf"]
USER pdf
WORKDIR /home/pdf

COPY Pipfile* ./
RUN pipenv install --deploy

COPY docker-entrypoint.sh .
COPY src src

EXPOSE 8080

ENTRYPOINT [ "/home/pdf/docker-entrypoint.sh" ]
CMD [ "pipenv", "run", "python", "src/server.py" ]
