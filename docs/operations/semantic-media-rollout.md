# Gestufter Rollout und Release-Gate für Semantic Media/Speech

Jede Stufe ist opt-in. Der Hub aktiviert sie erst nach einem schema-validen,
source-/configgebundenen `GO`. Fehlende oder externe nicht ausführbare Evidenz
ist `unverified` und damit `NO-GO`.

| Stufe | Eintritt | messbarer Stop | Rollback |
|---|---|---|---|
| `observe_only` | Contracts/Architektur/Security/Privacy/Build grün; nur content-free Read-Models | irgendein Leak, IDOR, Audit-Cardinality oder Mutation über Debug | Debugroute sperren, Events retentionbereinigen; Ordinary unverändert |
| `single_pair_opt_in` | Zwei Browserengines, Pair-E2EE, Live-Speech, Revoke/Stop und Ordinary-Fallback grün | E2EE-Downgrade, p95/p99 > 5 %, Stoploop, Consentfehler | Semantic Pair fence, Epoch/Keys rotieren, Ordinary weiterführen |
| `trusted_small_group` | Vier Rollen, Late Join/Leave/Rekey, SFU-Opaqueness und Mixed Mode grün | Payloadlesbarkeit, doppelte Lease, Receiverkopplung, Failoverfehler | Gruppe auf Ordinary, SFU drainen, Gruppenepoch erhöhen |
| `bounded_pilot` | Privacy-Game-Day, Chaos, SBOM/Scanner, Capacity/Performance und Runbooks grün | Security/Privacy Finding, Qualität, Budget oder Ressourcenlimit verletzt | neue Leases/Jobs sperren, Worker canceln, Semantic drainen |
| `general_opt_in` | Pilotfenster ohne Stopkriterium, Incidentbereitschaft und explizite Freigabe | jedes obige Signal oder Regression gegenüber gebundenem Pilotbaseline | auf bounded pilot/observe-only zurück, Ordinary niemals beenden |

Automatischer Stop reagiert auf Security-/Privacy-Fund, E2EE-Downgrade,
Live-p95/p99 über 1,05×, Qualitätsfehler, Budget > 100 % oder Ressourcenlimit.
Die einzige erlaubte Aktion am normalen Call ist `preserve`.

Command:

```bash
python scripts/run_semantic_media_program_release_gate.py --stage observe_only
```

Echte Live-Evidenz wird über explizite Inputreports bzw. `--execute-live-e2e`
eingebunden. Der Defaultlauf simuliert nichts und endet solange ehrlich `NO-GO`.
