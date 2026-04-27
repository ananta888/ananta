import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.ananta.mobile',
  appName: 'Ananta Control',
  webDir: 'dist/ananta-angular/browser',
  server: {
    // Keep Android WebView on http so local hub endpoints (http://127.0.0.1:5000)
    // are not blocked as mixed content.
    androidScheme: 'http'
  }
};

export default config;
