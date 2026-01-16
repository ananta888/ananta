FROM node:20-alpine

# Install curl and net-tools (which includes ifconfig, netstat, etc.)
# Using --retry to handle transient network issues
RUN apk add --no-cache --retry 5 curl net-tools

WORKDIR /app

# The rest of the setup is handled via volumes and command in docker-compose for development
