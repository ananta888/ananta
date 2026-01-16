FROM node:20-alpine

# Try to install curl and net-tools during build, but don't fail if network is restricted.
# We will try again at runtime in docker-compose if this fails.
RUN apk add --no-cache curl net-tools || true

WORKDIR /app

# The rest of the setup is handled via volumes and command in docker-compose for development
