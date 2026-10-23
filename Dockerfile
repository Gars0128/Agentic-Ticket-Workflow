FROM python:3.13-slim

WORKDIR /service
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY data data

# docker-compose.yml sets the command: the API or the worker.
