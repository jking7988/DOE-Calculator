# Dockerfile
FROM python:3.11-slim

# System deps (fonts for ReportLab PDFs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expose the port the app runs on
EXPOSE 8050

# Dash/Flask will be served by gunicorn
# IMPORTANT: use app:server (not app:app), since gunicorn needs the WSGI Flask server
CMD ["gunicorn", "--bind", "0.0.0.0:8050", "app:server"]

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      libreoffice-calc libreoffice-core libreoffice-common \
      fonts-dejavu fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea && \
    rm -rf /var/lib/apt/lists/*