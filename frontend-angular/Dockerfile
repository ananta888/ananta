# Using python:3.11-slim (Debian based) as it's already cached on the system 
# and Debian repositories (apt) are accessible in this network environment, 
# unlike Alpine's (apk).
FROM python:3.11-slim

# Install Node.js, NPM, curl, net-tools, ping and traceroute
RUN apt-get update && \
    apt-get install -y nodejs npm curl net-tools iputils-ping traceroute && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The rest of the setup is handled via volumes and command in docker-compose for development
