FROM node:20

# Install curl and net-tools (Debian based)
RUN apt-get update && \
    apt-get install -y curl net-tools && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The rest of the setup is handled via volumes and command in docker-compose for development
