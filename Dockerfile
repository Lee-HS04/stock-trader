# Use a Node.js image to avoid installing Node/NPM manually
FROM python:3.12-slim

# Install system dependencies required by OpenClaw
RUN apt-get update && apt-get install -y \
    curl \
    git \
    make \
    g++ \
    cmake \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Set up non-root user
RUN useradd -m sandboxuser
WORKDIR /home/sandboxuser/app

# Set permissions for npm
RUN mkdir /home/sandboxuser/.npm-global && \
    chown -R sandboxuser:sandboxuser /home/sandboxuser
ENV PATH=/home/sandboxuser/.npm-global/bin:$PATH
ENV NPM_CONFIG_PREFIX=/home/sandboxuser/.npm-global

USER sandboxuser

# Install OpenClaw globally
RUN npm install -g openclaw@latest

# install dependencies
RUN pip3 install massive-api-client pandas-ta requests-cache

CMD ["openclaw"]

# the command to run trader.py in the container: docker run -it --rm -v "$(pwd):/home/sandboxuser/app" openclaw-sandbox openclaw onboard