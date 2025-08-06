// run-e2e.cjs
const { exec } = require('child_process');

// Starte Docker-Container im Hintergrund
exec('docker-compose up -d', (err, stdout, stderr) => {
  if (err) {
    console.error('Fehler beim Starten der Docker-Container:', err);
    process.exit(1);
  }
  console.log(stdout);
  console.log('Docker-Container gestartet. Warte 10 Sekunden, bis die Backends bereit sind...');

  // Warte 10 Sekunden (ggf. anpassen falls länger nötig)
  setTimeout(() => {
    console.log('Starte die Playwright-Tests...');
    
    // Starte die E2E-Tests
    const testProcess = exec('npx playwright test', (err, stdout, stderr) => {
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
  }, 10000);
});

// Funktion zum Herunterfahren der Docker-Container
function cleanup() {
  exec('docker-compose down', (err, stdout, stderr) => {
    if (err) {
      console.error('Fehler beim Herunterfahren der Docker-Container:', err);
      process.exit(1);
    }
    console.log('Docker-Container gestoppt.');
  });
}