# Приём заявок с сайта: лёгкий образ на стандартной библиотеке Python
FROM python:3.12-slim

# без .pyc и лишнего кэша — образ меньше, старт быстрее
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Moscow

WORKDIR /app

# сначала только код: слой кэшируется, пока файл не менялся
COPY leads_api.py /app/leads_api.py

# папка журналов внутри контейнера (переживёт перезапуск через том)
RUN mkdir -p /app/logs && useradd -r -u 1001 leads && chown -R leads:leads /app
USER leads

EXPOSE 8099

# проверка живости: сервис сам отвечает на /health
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8099/health', timeout=4).status==200 else 1)"

CMD ["python", "/app/leads_api.py"]
