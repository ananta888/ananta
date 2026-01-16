FROM node:20-alpine

# Install curl and net-tools (which includes ifconfig, netstat, etc.)
# Using a different mirror and ensuring cache is updated to handle 'Permission denied' / DNS issues
RUN sed -i 's/http:\/\/dl-cdn.alpinelinux.org/https:\/\/dl-cdn.alpinelinux.org/g' /etc/apk/repositories && \
    apk update && \
    apk add --no-cache curl net-tools

WORKDIR /app

# The rest of the setup is handled via volumes and command in docker-compose for development
