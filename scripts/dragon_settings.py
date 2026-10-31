"""
Shared settings loader for the dragon-co2 automation scripts.

Each script calls get_settings('<script_name>', {..its own hard-coded
defaults..}) once, near the top, and reads its tuning numbers from the
dict that comes back instead of from literals.

Three layers, in priority order:

  1. USER OVERRIDE  (highest priority)
     Documents\\DragonCO2\\settings_override.json
     Lives OUTSIDE the project folder, in the user's Documents. Only
     contains the specific values the user has actually changed via
     settings_server.py / settings_ui.html. Because it lives outside the
     repo, a `git pull` (or any update that replaces the scripts/ folder)
     never touches it -- personal tuning always survives an update.

  2. SCHEMA DEFAULT
     scripts/settings_schema.json
     Ships with the project. Holds the factory default for every
     parameter plus the slider metadata used by the settings UI. This
     file is expected to change across updates (new params, new
     defaults) -- that's normal and fine, since it's not where personal
     overrides live.

  3. HARD-CODED FALLBACK  (lowest priority, but always available)
     The `fallback_defaults` dict passed in by the calling script -- the
     exact literal values that used to be hard-coded there. Used only if
     settings_schema.json is missing/corrupted/doesn't have this key, so
     a broken or missing settings file can never stop the bot from
     running.

To wipe all personal tuning (e.g. after a big update changes the
parameters significantly), delete Documents\\DragonCO2\\settings_override.json
or use "Reset ALL overrides" in settings_ui.html -- settings_schema.json
itself is never touched by the UI.
"""
import json
import os

_BASE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(_BASE, 'settings_schema.json')


def override_dir():
    """Documents\\DragonCO2 -- outside the project folder, survives updates/pulls."""
    return os.path.join(os.path.expanduser('~'), 'Documents', 'DragonCO2')


def override_path():
    return os.path.join(override_dir(), 'settings_override.json')


def _load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def get_settings(script_name, fallback_defaults):
    """Returns a dict {PARAM_NAME: value} for `script_name`, merging the
    three layers described above. The returned dict always has exactly
    the same keys as `fallback_defaults`."""
    schema_section = _load_json(SCHEMA_PATH).get(script_name, {})
    override_section = _load_json(override_path()).get(script_name, {})

    result = dict(fallback_defaults)
    for key in result:
        if key in override_section:
            result[key] = override_section[key]
        elif key in schema_section and isinstance(schema_section[key], dict) and 'default' in schema_section[key]:
            result[key] = schema_section[key]['default']
    return result
