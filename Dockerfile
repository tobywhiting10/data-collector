FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 DB_PATH=/data/bussage.db SONGS_FILE=/config/songs.yaml
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
# The song catalogue is baked into the image (edit songs.yaml, rebuild/redeploy).
# To change songs without rebuilding, mount your own file over /config/songs.yaml.
COPY songs.yaml /config/songs.yaml
RUN useradd -m bussage && mkdir -p /data && chown bussage /data
USER bussage
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
