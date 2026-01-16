FROM node:20-alpine

# Use a different Alpine mirror (Cloudflare) which often has better availability
# and try both http and https if one fails.
RUN echo "https://mirror.clarkson.edu/alpine/v3.20/main" > /etc/apk/repositories && \
    echo "https://mirror.clarkson.edu/alpine/v3.20/community" >> /etc/apk/repositories && \
    apk add --no-cache curl net-tools || \
    (echo "http://mirror.clarkson.edu/alpine/v3.20/main" > /etc/apk/repositories && \
     echo "http://mirror.clarkson.edu/alpine/v3.20/community" >> /etc/apk/repositories && \
     apk add --no-cache curl net-tools)

WORKDIR /app

# The rest of the setup is handled via volumes and command in docker-compose for development
