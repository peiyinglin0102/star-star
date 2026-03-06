# db_config.py
import os, pymysql
def get_conn():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        user=os.getenv("DB_USER", "winnie"),
        password=os.getenv("DB_PASS", "winnie2025!"),
        database=os.getenv("DB_NAME", "starstar"),
        port=int(os.getenv("DB_PORT", "3306")),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )