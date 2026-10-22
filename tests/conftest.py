"""Keep tests independent of a developer's local .env."""

import os

# Environment variables outrank .env, so a configured model never leaks into tests.
# Tests that exercise the model pass openai_* settings explicitly.
os.environ["OPENAI_MODEL"] = ""
os.environ["OPENAI_API_KEY"] = ""
