import pandas as pd
import sqlite3

# Read the Excel file
df = pd.read_excel('MSC_Parts.xlsx', sheet_name='Items')

# Connect to (or create) the SQLite database
conn = sqlite3.connect('inventory.db')

# Write to a table (creates it if it doesn't exist)
df.to_sql('part_items', conn, if_exists='append', index=False)

print("Import Successful")

conn.close()