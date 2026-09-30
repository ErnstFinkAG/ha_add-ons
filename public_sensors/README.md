# Public Sensors

Public Sensors publishes selected Home Assistant data as standalone, read-only web pages.

Version 0.4 uses a universal page definition. Every public page has:

- a URL/path;
- a public name;
- a full YAML field; and
- an entity ID field.

Exactly one of `yaml` or `entity_id` must be filled for each page.

The repository default contains no sensor entities:

    pages: []

This lets a fresh installation start cleanly on any Home Assistant instance. Add your own pages through the app configuration after installation.

Example URLs:

- `https://public.example.com/outside-temperature/`
- `https://public.example.com/energy/`

The optional index at `/` lists all configured pages.

## Security design

Public Sensors separates collection from serving:

1. A private collector receives the Home Assistant Supervisor token and reads only configured entities/history.
2. The collector writes a sanitized JSON cache.
3. The public web process runs as an unprivileged user with a clean environment and no Supervisor/Home Assistant environment variables.
4. The public process reads only the sanitized cache and does not read the private app options.

The raw YAML configuration is not copied to the public cache.

See `DOCS.md` for configuration, YAML behavior, and reverse-proxy setup.
