
import sys, os
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
from appyhour_lib.paths import gelpack_root  # noqa: E402
sys.path.insert(0, str(gelpack_root()))  # sibling repo since 2026-09-12 (R-35)
from google_integration import GoogleIntegration
# No path -> resolves via appyhour_lib.credentials (inline JSON or key file).
gi = GoogleIntegration()
email = gi.test_connection()
print(f'Connected as: {email}')

# List shared drives
result = gi._drive.drives().list(pageSize=20).execute()
drives = result.get('drives', [])
print(f'Shared Drives: {len(drives)}')
for d in drives:
    print(f'  {d["id"]} | {d["name"]}')
