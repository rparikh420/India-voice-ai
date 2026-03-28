# Gujarati Voice AI Agent - Production Dockerfile
# Base: Python 3.12 slim with system deps for lxml

FROM python:3.12-slim

# Install system dependencies (gcc, libxml2, libxslt for lxml)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY *.py ./
COPY frontend/ ./frontend/

# Run the agent (production mode)
CMD ["python", "agent.py", "start"]
