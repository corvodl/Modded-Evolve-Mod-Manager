"""UI labels come from source/ui_text.json at build time. Developer-only copy."""
from __future__ import annotations
import json
from pathlib import Path

BUNDLED_TEXT = Path(__file__).resolve().parent / 'ui_text.json'

def load_text():
    """Load bundled labels without any externally editable player-facing override."""
    try:
        raw = json.loads(BUNDLED_TEXT.read_text(encoding='utf-8-sig'))
        if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
            raise ValueError('UI text must be a JSON object containing string values.')
    except (OSError, ValueError) as error:
        return {}, f'Bundled UI labels unavailable: {error}'
    return raw, ''
