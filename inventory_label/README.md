# Inventory Label add-on rewrite

This is a clean rewrite scaffold for a Home Assistant add-on that stores:

- `labelprofiles`: printer-specific label definitions
- `labelfields`: shared field definitions applied to every profile

The current rewrite focuses on:

- a clean add-on config model
- validation and normalization
- translation labels for the add-on config screen
- a lightweight ingress page for debugging the resolved config

It does **not** yet implement the final print pipeline, preview renderer, or field editing UI beyond the Home Assistant add-on configuration form.
