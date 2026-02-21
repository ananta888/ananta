# Mobile Features (Capacitor Android)

Die App nutzt Capacitor bereits als Container. Diese Erweiterungen sind nun enthalten:

- Runtime-Erkennung (`native` vs. Browser)
- Online/Offline-Erkennung mit UI-Hinweis
- Dashboard-Fallback auf gecachte Read-Models bei Offline-Fehlern
- Push-Berechtigungs-Initialisierung ueber Browser Notification API

## Relevante Dateien

- `frontend-angular/src/app/services/mobile-runtime.service.ts`
- `frontend-angular/src/app/services/hub-config-api.client.ts`
- `frontend-angular/src/app/app.component.ts`

## Android Build

```bash
cd frontend-angular
npm run android:prepare
```

## Hinweise zu Push

Fuer produktive native Push-Notifications (FCM/APNS) ist zusaetzlich das Capacitor Push Plugin und die jeweilige Plattformkonfiguration (Firebase/APNS Keys) erforderlich.
