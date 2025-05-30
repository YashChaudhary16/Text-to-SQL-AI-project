import sqlite3
import pandas as pd

def load_database_schema(conn: sqlite3.Connection) -> str:
    """
    Returns a human-readable schema summary for all tables in the SQLite database.
    """
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        tables = [row[0] for row in cursor.fetchall()]
        if not tables:
            return "No tables found in the database."
        schema = []
        for table in tables:
            schema.append(f"Table: {table}")
            cursor.execute(f"PRAGMA table_info({table});")
            columns = cursor.fetchall()
            for col in columns:
                # col[1]: name, col[2]: type
                schema.append(f"  - {col[1]} ({col[2]})")
        return "\n".join(schema)
    except Exception as e:
        return f"Error reading schema: {e}"

def run_query(conn: sqlite3.Connection, query: str) -> pd.DataFrame:
    """
    Executes a SQL query and returns the result as a pandas DataFrame.
    Raises an exception if the query fails.
    """
    try:
        return pd.read_sql_query(query, conn)
    except Exception as e:
        raise RuntimeError(f"Query failed: {e}")
