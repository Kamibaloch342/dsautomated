import os
import sys

# Add Automations to path so we can import utils.config
_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_DIR))

from utils import config
from google.oauth2 import service_account
from googleapiclient.discovery import build

creds = service_account.Credentials.from_service_account_file(
    config.GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE,
    scopes=["https://www.googleapis.com/auth/spreadsheets"],
)
svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

spreadsheet = svc.spreadsheets().get(spreadsheetId=config.GOOGLE_SHEET_ID).execute()
print(f"Spreadsheet Title: {spreadsheet.get('properties', {}).get('title')}")
total_cells = 0
for sheet in spreadsheet.get('sheets', []):
    props = sheet['properties']
    title = props.get('title')
    grid = props.get('gridProperties', {})
    rows = grid.get('rowCount', 0)
    cols = grid.get('columnCount', 0)
    cells = rows * cols
    total_cells += cells
    print(f"Tab '{title}': {rows} rows x {cols} cols = {cells} cells")

print(f"\nTotal Cells in Workbook: {total_cells}")
