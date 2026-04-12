"""Re-run enrichment with improved customer-email-based Gorgias search."""
import sys
import json
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "GelPackCalculator"))

from gorgias_sheets_sync import enrich_incomplete_rows

if __name__ == "__main__":
    try:
        print("Running enrichment (customer-email search)...", flush=True)
        result = enrich_incomplete_rows(dry_run=False)
        print(json.dumps(result, indent=2), flush=True)
        print("DONE", flush=True)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
