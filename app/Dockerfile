FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

COPY requirements.txt /srv/requirements.txt
RUN pip install -r requirements.txt

COPY stockintel /srv/stockintel
COPY static /srv/static
COPY prompts /srv/prompts

EXPOSE 8095

CMD ["uvicorn", "stockintel.main:app", "--host", "0.0.0.0", "--port", "8095", "--access-log"]
