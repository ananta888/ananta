import os
import json
import psycopg2
from psycopg2.extras import Json

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@db:5432/ananta")


def get_conn(retries=3, delay=1.0):
