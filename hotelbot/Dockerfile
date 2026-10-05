FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY data ./data
COPY migrations ./migrations
COPY alembic.ini .

RUN useradd --create-home --uid 10001 hotelbot
USER hotelbot

EXPOSE 8000
# The app migrates on startup (HOTELBOT_AUTO_MIGRATE=true). To migrate as a
# separate step instead: `alembic upgrade head` with HOTELBOT_AUTO_MIGRATE=false.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
