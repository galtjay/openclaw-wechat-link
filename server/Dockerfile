# 使用官方自带 uv 的镜像
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

# Set the working directory
WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy project specification files first
COPY pyproject.toml uv.lock ./

# Install the dependencies. Pre-installing here ensures downloading happens during build,
# not every time the container starts up.
RUN uv sync --frozen --no-dev

# Copy the application code
# package-lock.json is for Node.js, not needed for Python, but we copy it if you have frontend tools
COPY index.html main.py package-lock.json ./

# Expose the port the app runs on
EXPOSE 8000

# Command to run the application using uv run
CMD ["uv", "run", "main.py"]
