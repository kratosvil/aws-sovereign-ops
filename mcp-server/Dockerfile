FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer cache friendly)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

ENV PYTHONUNBUFFERED=1
ENV PORT=8080

EXPOSE 8080

# Non-root user — no shell, no escalation surface
RUN adduser --disabled-password --no-create-home appuser
USER appuser

CMD ["python", "server.py"]
