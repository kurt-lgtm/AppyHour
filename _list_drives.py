
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath('.')), 'GelPackCalculator'))
sys.path.insert(0, 'GelPackCalculator')
from google_integration import GoogleIntegration
gi = GoogleIntegration('shipping-perfomance-review-accd39ac4b78.json')
email = gi.test_connection()
print(f'Connected as: {email}')

# List shared drives
result = gi._drive.drives().list(pageSize=20).execute()
drives = result.get('drives', [])
print(f'Shared Drives: {len(drives)}')
for d in drives:
    print(f'  {d["id"]} | {d["name"]}')
