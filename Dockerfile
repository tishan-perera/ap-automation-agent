FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Copy only what the app needs, so local files like .env never end up in the image.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --upgrade pip && pip install .

RUN mkdir -p /srv/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
