FROM node:20-alpine

# Install curl and net-tools (Alpine based)
# We use a specific mirror and --no-cache to avoid 'Permission denied' errors 
# which are often caused by DNS/Network issues in the build environment.
RUN apk add --no-cache --repository http://dl-cdn.alpinelinux.org/alpine/v3.20/main/ --repository http://dl-cdn.alpinelinux.org/alpine/v3.20/community/ curl net-tools

WORKDIR /app

# The rest of the setup is handled via volumes and command in docker-compose for development
