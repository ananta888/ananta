# AI Assistant Settings Scope

## Ziel
Der AI-Assistant soll alle Einstellungen aendern koennen, die ein Admin auch in der Web-UI aendern kann.

## Kontext, der an das Modell geht
- `settings.summary`: komprimierte aktuelle Konfiguration (LLM, System, Quality Gates, Counts)
- `settings.editable_inventory`: editierbare Settings inkl. `key`, `path`, `type`, `endpoint`
- `automation`: Status von `autopilot`, `auto_planner`, `triggers`
- `assistant_capabilities`: erlaubte Tools laut Capability-Contract und Benutzerrolle

## Mutations-Tools (Assistant)
- `update_config`
- `create_template`, `update_template`, `delete_template`
- `upsert_team_type`, `delete_team_type`
- `upsert_role`, `delete_role`
- `link_role_to_team_type`, `unlink_role_from_team_type`, `set_role_template_mapping`
- `upsert_team`, `delete_team`, `activate_team`
- `configure_auto_planner`
- `configure_triggers`
- `set_autopilot_state`

## Schutzmechanismen
- Alle mutierenden Tools sind admin-pflichtig.
- Tool-Ausfuehrungen laufen weiterhin ueber bestaetigungspflichtige Plaene im Assistant.
- Capability-Contract und Guardrails blockieren unzulaessige/nicht erlaubte Tools.
