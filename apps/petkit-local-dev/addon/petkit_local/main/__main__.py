"""`python3 -m petkit_local.main`, which is what the add-on's Dockerfile runs.

The console script declared in `pyproject.toml` calls `main()` directly; this is
the same entry point for the module form, so both paths start identically.
"""
from petkit_local.main import main

if __name__ == "__main__":
    main()
