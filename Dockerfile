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

# Switch to root to perform system-level tasks. We do this to keep huge installs like openclaw install at the top so that they can be cached, no need to reinstall when we rebuild docker image
USER root

RUN mkdir -p /home/sandboxuser/.npm-global/lib/node_modules/openclaw/skills/stock-trader

COPY SKILL.md /home/sandboxuser/.npm-global/lib/node_modules/openclaw/skills/stock-trader/SKILL.md
COPY trading_data.py /home/sandboxuser/app/trading_data.py
COPY sanity_checker.py /home/sandboxuser/app/sanity_checker.py
COPY trade_executor.py /home/sandboxuser/app/trade_executor.py

# Give ownership of all files to the sandboxuser
RUN chown -R sandboxuser:sandboxuser /home/sandboxuser/app && \
    chown -R sandboxuser:sandboxuser /home/sandboxuser/.npm-global/lib/node_modules/openclaw/skills/stock-trader

# Switch back to the non-root user for security
USER sandboxuser

CMD ["openclaw"]

# the command to set up openclaw with the skill in the container: docker run -it --rm -v "$(pwd):/home/sandboxuser/app" -v "$(pwd)/.openclaw_state:/home/sandboxuser/.openclaw" openclaw-sandbox openclaw onboard
# the command to check if openclaw detects the skill in the container: docker run -it --rm -v "$(pwd):/home/sandboxuser/app" openclaw-sandbox openclaw skills list
# the command to run openclaw in the container: docker run -it --rm -v "$(pwd):/home/sandboxuser/app" -v "$(pwd)/.openclaw_state:/home/sandboxuser/.openclaw" -e MASSIVE_API_KEY="[API_KEY]" -e ZAI_API_KEY="your_glm_api_key_here" openclaw-sandbox /bin/bash -c "openclaw gateway --force & openclaw tui"
# the command to get a command prompt terminal in the container: 
