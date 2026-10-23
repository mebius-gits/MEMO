"""Keep tests independent of a developer's local .env."""

import os

# Environment variables outrank .env, so a configured model never leaks into tests.
# Tests that exercise the model pass openai_* settings explicitly.
os.environ["OPENAI_MODEL"] = ""
os.environ["OPENAI_API_KEY"] = ""
# Quiet hours from a local .env would make proactive tests depend on the time of day.
os.environ["QUIET_HOURS"] = ""
# A locally running SearXNG must not answer tests; search tests inject a fake searcher.
os.environ["SEARCH_URL"] = ""
