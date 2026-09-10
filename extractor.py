import pandas as pd
from openpyxl import load_workbook

input_file = "MSC_Audit.xlsx"
output_file = "compiled.xlsx"
target_columns = ['Part Number', 'Description', 'Serial Number', 'Rack', 'Tray', 'Type', 'Customer', 'Price USD']

# Load workbook with openpyxl just to check sheet visibility
wb = load_workbook(input_file, read_only=True)

visible_sheets = [
    sheet.title for sheet in wb.worksheets
    if sheet.sheet_state == "visible"
]

print("Visible sheets:", visible_sheets)
print("Skipped (hidden):", [s.title for s in wb.worksheets if s.sheet_state != "visible"])

compiled_rows = []

for sheet_name in visible_sheets:
    df = pd.read_excel(input_file, sheet_name=sheet_name)

    cols_present = [c for c in target_columns if c in df.columns]
    missing = set(target_columns) - set(cols_present)
    if missing:
        print(f"Sheet `{sheet_name}` is missing columns: {missing}")

    if cols_present:
        temp = df[cols_present].copy()
        temp["Source Sheet"] = sheet_name  # track which sheet each row came from
        compiled_rows.append(temp)

if compiled_rows:
    result_df = pd.concat(compiled_rows, ignore_index=True)
    result_df.to_excel(output_file, index=False)
    print(f"Done. Saved to {output_file}")
else:
    print("No matching data found — nothing was saved.")