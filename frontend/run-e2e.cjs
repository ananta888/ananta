// run-e2e.cjs
const { exec } = require('child_process');
const http = require('http');
const path = require('path');

const rootDir = path.resolve(__dirname, '..');

// Helfer zum Warten auf den Controller-Endpunkt
function waitFor(url, { retries = 30, interval = 1000 } = {}) {
  return new Promise((resolve, reject) => {
    const attempt = () => {
      http
        .get(url, res => {
          // Wir brauchen den Body nicht, müssen ihn aber konsumieren
          res.resume();
          if (res.statusCode === 200) {
            resolve();
          } else if (retries > 0) {
            setTimeout(() => attempt(--retries), interval);
          } else {
            reject(new Error(`Unexpected status ${res.statusCode}`));
          }
        })
        .on('error', err => {
          if (retries > 0) {
            setTimeout(() => attempt(--retries), interval);
          } else {
            reject(err);
          }
        });
    };
    attempt();
  });
}

// Starte Docker-Container im Hintergrund
exec('docker-compose up -d', { cwd: rootDir }, async (err, stdout, stderr) => {
  if (err) {
    console.error('Fehler beim Starten der Docker-Container:', err);
    process.exit(1);
  }
  console.log(stdout);
  console.log('Docker-Container gestartet. Warte, bis der Controller erreichbar ist...');

  try {
    await waitFor('http://localhost:8081/config');
  } catch (e) {
    console.error('Controller nicht erreichbar:', e);
    cleanup();
    process.exit(1);
  }

  console.log('Starte die Playwright-Tests...');

  // Starte die E2E-Tests
  const testProcess = exec('npx playwright test', { cwd: __dirname }, (err, stdout, stderr) => {
    if (err) {
      console.error('Fehler beim Ausführen der Tests:', err);
      cleanup();
      process.exit(1);
    }
    console.log(stdout);
    cleanup();
  });

  // Leitet die Ausgabe des Testprozesses weiter:
  testProcess.stdout.pipe(process.stdout);
  testProcess.stderr.pipe(process.stderr);
});

// Funktion zum Herunterfahren der Docker-Container
function cleanup() {
  exec('docker-compose down', { cwd: rootDir }, (err, stdout, stderr) => {
    if (err) {
      console.error('Fehler beim Herunterfahren der Docker-Container:', err);
      process.exit(1);
    }
    console.log('Docker-Container gestoppt.');
  });
}