import os

# Keep tests independent of the repo's printer.env tuning values.
os.environ.setdefault("PC1_DISABLE_ENV_FILE", "1")
