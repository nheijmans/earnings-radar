FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY earnings_radar.py follow_list.json ./

# Non-root; /data holds the SQLite state (mount a volume there).
RUN useradd -u 10001 radar && mkdir /data && chown radar /data
USER radar

ENTRYPOINT ["python", "earnings_radar.py"]
