FROM node:20-slim

WORKDIR /app
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Dependencies kopieren und installieren (Caching nutzen)
COPY package*.json ./
RUN npm ci --no-audit --no-fund
RUN npx playwright install --with-deps chromium

# Der Rest wird via Volume gemountet, aber wir kopieren ihn für das Image-Building
COPY . .

EXPOSE 4200

# Startbefehl für Development
CMD ["npx", "ng", "serve", "--host", "0.0.0.0", "--port", "4200", "--poll", "2000"]
