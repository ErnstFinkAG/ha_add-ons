# Public Sensors

Public Sensors publishes selected Home Assistant data as standalone, read-only web pages.

Version 0.4 uses a universal page definition. Every public page has:

- a URL/path;
- a public name;
- a full YAML field; and
- an entity ID field.

Exactly one of `yaml` or `entity_id` must be filled for each page.

Example URLs:

- `https://public.example.com/groundwater/`
- `https://public.example.com/outside-temperature/`

The optional index at `/` lists all configured pages.

## Security design

Public Sensors now separates collection from serving:

1. A private collector receives the Home Assistant Supervisor token and reads only the configured entities/history.
2. The collector writes a sanitized JSON cache.
3. The public web process runs as an unprivileged user with `SUPERVISOR_TOKEN` removed.
4. The public process reads only the sanitized cache and does not read the private add-on options.

The raw YAML configuration is not copied to the public cache.

See `DOCS.md` for configuration, YAML behavior, and reverse-proxy setup.
