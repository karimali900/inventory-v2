# -*- coding: utf-8 -*-
"""Entry point for the Inventory Management System."""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DEV_MODE", "1")  # Allow missing env vars in dev

from inventory_app import app

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8100))
    reload_flag = os.environ.get("RELOAD", "true").lower() == "true"
    uvicorn.run("inventory_app:app", host="0.0.0.0", port=port, reload=reload_flag)
