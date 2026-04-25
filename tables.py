import pandas as pd
import os
MIMIC_DATASET_PATH = "mimic-demo"

tables = {}

for file in os.walk(MIMIC_DATASET_PATH):
    for filename in file[2]:
        if filename.endswith(".csv"):
            df = pd.read_csv(os.path.join(file[0], filename), low_memory=False)
            tables[filename[:-4]] = df


def get_available_tables():

    tables_info = []

    for dataframe in tables:
        table_info = {
            "table_name": dataframe,
            "columns": list(tables[dataframe].columns)
        }
        tables_info.append(table_info)

    return tables_info


def filter_table(table_name, filters):
    if table_name not in tables:
        return f"Table {table_name} not found."

    df = tables[table_name]

    for filter in filters:
        column_name = filter.get("column_name")
        value = filter.get("value")

        if column_name not in df.columns:
            return f"Column {column_name} not found in table {table_name}."

        df = df.loc[df[column_name].astype(str) == str(value)]

    return df

def get_table(table_name):
    if table_name not in tables:
        return f"Table {table_name} not found."

    return tables[table_name]