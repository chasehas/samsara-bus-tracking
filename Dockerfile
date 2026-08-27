# Python lightweight container for 24/7 School Bus Tracking
FROM python:3.12-slim

# Force Python output to be unbuffered so logs stream instantly
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Run the live alerting service
CMD ["python", "main.py"]
