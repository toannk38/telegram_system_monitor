FROM python:3.10.14-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    sudo \
    procps \
    bc \
    htop \
    nano \
    curl \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy only requirements for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Default command (will be overridden by docker-compose)
CMD ["python", "--version"]