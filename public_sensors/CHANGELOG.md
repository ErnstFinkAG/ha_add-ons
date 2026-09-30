# Changelog

## 0.3.1

- Add Home Assistant image metadata labels for app version, type, and architecture.
- Declare BUILD_VERSION and BUILD_ARCH in the Dockerfile so locally built images carry the Supervisor build metadata.

## 0.3.0

- Add an optional index page at the root URL.
- Add the `show_index` configuration switch.
- The index lists all enabled Public Sensors pages as links.
- Keep direct sensor pages unchanged.

## 0.2.0

- Add multiple independently configured public pages.
- Use a path for each page, for example `/groundwater/`.
- Add an enable switch for each page.
- Add configurable decimal places per page.
- Keep the root path unpublished and return 404 for unknown paths.
- Keep graph data below the same public path, for example `/groundwater/data`.
- Preserve compatibility with the original 0.1.x single groundwater configuration.

## 0.1.0

- Initial Public Sensors release.
- Read Home Assistant history through the internal Core API.
- Publish one numeric history series and one optional ON/OFF series.
- Five-minute numeric aggregation by default.
- Fixed, read-only canvas graph with hover tooltips.
- No Home Assistant token or credentials are sent to the browser.
