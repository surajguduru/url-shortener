import os

# Must be set before app modules are imported: settings are read at import time.
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("APP_VERSION", "test")
