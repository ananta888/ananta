import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.ananta.mobile',
  appName: 'Ananta Control',
  webDir: 'dist/ananta-angular/browser',
  server: {
    androidScheme: 'https'
  }
};

export default config;
