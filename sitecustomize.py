import sys

import ai_provider_runtime

# Keep legacy imports working while the runtime provider module is the source of truth.
sys.modules.setdefault("ai_provider", ai_provider_runtime)
