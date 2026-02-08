import os
import sys
import sqlite3
# Ensure project root on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.database import DATABASE_URL

def main():
    if not DATABASE_URL.startswith('sqlite:///'):
        print('Non-sqlite URL; skipping. DATABASE_URL=', DATABASE_URL)
        return
    db_path = DATABASE_URL.replace('sqlite:///','')
    print('DB path:', db_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute('CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)')
    try:
        cur.execute('SELECT version_num FROM alembic_version')
        row = cur.fetchone()
        print('alembic_version before:', row)
    except Exception as e:
        print('select error:', e)
    cur.execute('DELETE FROM alembic_version')
    cur.execute('INSERT INTO alembic_version (version_num) VALUES (?)', ["1c2d3e4f5a6b"])  # current head
    con.commit()
    cur.execute('SELECT version_num FROM alembic_version')
    print('alembic_version after:', cur.fetchone())
    con.close()

if __name__ == '__main__':
    main()
