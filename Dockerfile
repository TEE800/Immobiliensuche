FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mietsuche ./mietsuche
COPY config.example.yaml anschreiben.example.txt ./

# Konfiguration, Anschreiben und Datenbank liegen im Volume /data.
VOLUME /data
ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "[ -f /data/config.yaml ] || cp config.example.yaml /data/config.yaml; [ -f /data/anschreiben.txt ] || cp anschreiben.example.txt /data/anschreiben.txt; exec python -m mietsuche --serve --config /data/config.yaml"]
