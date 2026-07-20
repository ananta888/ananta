FROM node:24.18.0-slim@sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d

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
