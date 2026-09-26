# Use Python 3.11 slim image
FROM python:3.11-slim

# Install system dependencies including ffmpeg and unzip (for deno).
# fonts-noto-color-emoji lets libass render colour emojis in burned captions.
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    unzip \
    fonts-noto-color-emoji \
    fontconfig \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

# Install deno (required by yt-dlp for YouTube JS challenge solving)
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_DIR=/root/.deno
ENV PATH="/root/.deno/bin:${PATH}"

# Install uv package manager
RUN pip install uv

# Set working directory
WORKDIR /app

# Copy pyproject.toml and uv.lock for dependency installation
COPY backend/pyproject.toml backend/uv.lock* ./

# Create virtual environment and install dependencies using uv sync
RUN uv venv .venv && uv sync

# Force update yt-dlp to the absolute latest version to handle YouTube API changes
# Also install from git to get the very latest commits
RUN uv pip install --upgrade --force-reinstall yt-dlp
RUN uv pip install --upgrade --force-reinstall "yt-dlp[default]"

# Copy source code
COPY backend/src/ ./src/

# Copy fonts and transitions directories
COPY backend/fonts/ ./fonts/
COPY backend/transitions/ ./transitions/

# Copy the versioned local assets bundle (audio beds, SFX, B-roll). Sound
# design and B-roll resolution are 100% offline, so the files are baked in:
# a self-hosted render must never depend on a download (spec #12, T4).
COPY assets/ ./assets/

# Create necessary directories for video processing
RUN mkdir -p /app/uploads /app/clips /app/logs /tmp

# Expose the port FastAPI will run on
EXPOSE 8000

# Activate virtual environment and start the application
CMD [".venv/bin/uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
