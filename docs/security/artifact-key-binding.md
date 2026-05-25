# Artifact Key Binding (User/Device)

## Trennung von Identitaet und Schluesselmaterial

- OIDC bestaetigt User-Identitaet und Session-Claims.
- Device-Key bestaetigt Besitz eines konkreten Endgeraets.
- OIDC ersetzt keinen privaten Device-Key.

## Binding-Modell

- User kann ein oder mehrere Device-Keys registrieren (Enrollment).
- Grants koennen optional auf User + Device-Key gebunden werden.
- Sensible Klassen (`restricted`, `secret`, `local_only`) sollten Device-Binding erfordern.

## Enrollment

1. Authentifizierter User startet Enrollment.
2. Device erzeugt lokal ein Schluesselpaar.
3. Nur der Public Key wird beim Hub hinterlegt.
4. Hub verknuepft Public Key mit User und Device-Metadaten.

## Rotation und Verlustfall

- Rotation erstellt neuen Device-Key und markiert alten als auslaufend/revoked.
- Bei Verlust/Diebstahl wird Device-Key sofort revokiert.
- Grants, die an den alten Key gebunden sind, muessen neu ausgestellt werden.

## Schluessel-Sicherheitsregel

- Private Keys verlassen das Device nicht im Klartext.
- Signatur-/Unwrap-Operationen passieren lokal oder in gesicherten Device-Boundary-Komponenten.
