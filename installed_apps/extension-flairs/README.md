# User Flairs Extension

Adds customizable user titles, flairs, and badges to member profiles and admin
views. This extension is also the **reference example** for writing an
AppManager extension — it exercises every part of the extension contract.

## What it demonstrates

| Contract surface | How it's used here |
|---|---|
| **Entry point** (`extension:extension`) | A Flask app the host loads at startup |
| **UI slots** (`ui_slots: ["user_badge"]`) | Registers a `user_badge` slot so the host renders a flair badge next to any member's name |
| **Admin blueprint** (`admin_sections`) | Ships a custom admin panel (`flairs_admin:bp`) the host mounts at `/admin/apps/extension-flairs/assign` |
| **Settings schema** (`settings_schema`) | Declares typed settings (color, integer, boolean) the host renders as a generated form |
| **Extension data** (`client.get_data/set_data`) | Stores each user's flair as per-entity JSON in the `app_extension_data` table |

## Layout

```
extension-flairs/
├── manifest.json        # the contract: identity, slots, admin_sections, settings_schema
├── extension.py         # entry point: flair logic, presets, badge render, slot registration
├── flairs_admin.py      # custom admin blueprint (declared in admin_sections)
└── templates/
    └── flairs_admin/
        └── assign.html  # admin panel template (extends host base.html, uses app.css)
```

## How to write your own extension

### 1. `manifest.json` — declare the contract

```json
{
  "name": "My Extension",
  "slug": "my-extension",
  "version": "1.0.0",
  "app_type": "extension",
  "target_app": "appmanager",
  "has_web_ui": false,
  "entry_point": "extension:extension",
  "health_check_path": "/health",
  "ui_slots": ["user_badge"],
  "admin_sections": [
    {"id": "panel", "label": "My Panel", "icon": "tag",
     "blueprint": "my_admin:bp", "order": 10}
  ],
  "settings_schema": [
    {"key": "accent_color", "type": "color", "label": "Accent color", "default": "#F5A524"},
    {"key": "max_items", "type": "integer", "label": "Max items", "default": 5},
    {"key": "enabled", "type": "boolean", "label": "Enabled", "default": true}
  ]
}
```

### 2. Entry point — a Flask app + the SDK

```python
from flask import Flask
from appmanager.sdk import AppManagerClient

extension = Flask(__name__)
client = AppManagerClient("my-extension")


def render_badge(user_id):
    from markupsafe import Markup

    return Markup("<span>…</span>")


client.register_slot("user_badge", render_badge, priority=10)
```

### 3. Admin blueprint — a custom panel

Declare it in `admin_sections` with `"blueprint": "module:bp"`. The host mounts
it at `/admin/apps/<slug>/<panel_id>` and **enforces admin auth at mount time** —
you never self-guard. Reuse the host's `app.css` by extending `base.html`.

### 4. Settings — generated form, zero code

Declare `settings_schema` and the host renders a typed form, persists values to
`app_configs`, and you read them with `client.get_setting('key', default)`.

### 5. Data — per-entity JSON

Use `client.get_data(entity_type, entity_id)` and
`client.set_data(entity_type, entity_id, dict)` to store structured data.

## Host delegation

The host's `appmanager/extensions.py` delegates flair lookups to this module
(`get_user_flair`, `set_user_flair`, `render_user_flair_badge`). If this
extension is not installed, the host degrades gracefully to "no flair" — the
`user_badge` slot simply renders nothing.
