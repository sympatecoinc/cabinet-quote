#!/usr/bin/env python3
"""
Migrate data from local SQLite to Cloud SQL PostgreSQL.
Usage: python migrate_data.py

Requires env vars: CLOUD_SQL_CONNECTION_NAME, DB_USER, DB_PASS, DB_NAME
"""

import sqlite3
import json
import os
from google.cloud.sql.connector import Connector

# SQLite source
SQLITE_PATH = os.path.join(os.path.dirname(__file__), 'cabinet_quoter.db')
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), 'company_settings.json')


def get_pg_conn():
    connector = Connector()
    return connector.connect(
        os.environ['CLOUD_SQL_CONNECTION_NAME'],
        "pg8000",
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ['DB_PASS'],
        db=os.environ.get('DB_NAME', 'cabinet_quoter'),
    )


def migrate():
    # Connect to both databases
    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    pg_conn = get_pg_conn()
    pg_cur = pg_conn.cursor()

    # Migrate projects
    rows = sqlite_conn.execute('SELECT * FROM projects ORDER BY id').fetchall()
    print(f"Migrating {len(rows)} projects...")
    for row in rows:
        r = dict(row)
        pg_cur.execute(
            '''INSERT INTO projects (name, project_type, cabinets, data, calc_params, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)''',
            (r['name'], r.get('project_type', 'standard'), r.get('cabinets', '[]'),
             r.get('data'), r.get('calc_params', '{}'), r['created_at'], r['updated_at'])
        )

    # Migrate kitchen_templates
    rows = sqlite_conn.execute('SELECT * FROM kitchen_templates ORDER BY id').fetchall()
    print(f"Migrating {len(rows)} kitchen templates...")
    for row in rows:
        r = dict(row)
        pg_cur.execute(
            '''INSERT INTO kitchen_templates (name, description, cabinets, project_id, is_global, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)''',
            (r['name'], r.get('description', ''), r.get('cabinets', '[]'),
             r.get('project_id'), r.get('is_global', 0), r['created_at'], r['updated_at'])
        )

    # Migrate standard_cabinets
    rows = sqlite_conn.execute('SELECT * FROM standard_cabinets ORDER BY id').fetchall()
    print(f"Migrating {len(rows)} standard cabinets...")
    for row in rows:
        r = dict(row)
        pg_cur.execute(
            '''INSERT INTO standard_cabinets (name, code, type, width, height, depth,
                has_doors, num_doors, has_drawers, num_drawers, has_shelves, num_shelves,
                has_false_drawers, num_false_drawers, has_dividers, num_dividers,
                has_pullout_shelves, num_pullout_shelves, use_axial_drawers, edgebanding_type,
                project_id, is_global, panel_sides, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
            (r['name'], r.get('code', ''), r.get('type', 'Base Cabinets'),
             r.get('width', 24), r.get('height', 34.5), r.get('depth', 24),
             r.get('has_doors', 0), r.get('num_doors', 0),
             r.get('has_drawers', 0), r.get('num_drawers', 0),
             r.get('has_shelves', 0), r.get('num_shelves', 0),
             r.get('has_false_drawers', 0), r.get('num_false_drawers', 0),
             r.get('has_dividers', 0), r.get('num_dividers', 0),
             r.get('has_pullout_shelves', 0), r.get('num_pullout_shelves', 0),
             r.get('use_axial_drawers', 1), r.get('edgebanding_type', '1.0mm PVC'),
             r.get('project_id'), r.get('is_global', 0), r.get('panel_sides', 1),
             r['created_at'], r['updated_at'])
        )

    # Migrate pricing_rules
    rows = sqlite_conn.execute('SELECT * FROM pricing_rules ORDER BY id').fetchall()
    print(f"Migrating {len(rows)} pricing rules...")
    for row in rows:
        r = dict(row)
        pg_cur.execute(
            '''INSERT INTO pricing_rules (name, description, markup_primary, markup_back,
                markup_door_drawer, markup_drawer_material, markup_hardware, markup_edgebanding,
                material_usage_buffer, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
            (r['name'], r.get('description', ''),
             r.get('markup_primary', 0), r.get('markup_back', 0),
             r.get('markup_door_drawer', 0), r.get('markup_drawer_material', 0),
             r.get('markup_hardware', 0), r.get('markup_edgebanding', 0),
             r.get('material_usage_buffer', 0), r['created_at'], r['updated_at'])
        )

    # Migrate company_settings from JSON file
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH, 'r') as f:
            settings = json.load(f)
        print("Migrating company settings...")
        pg_cur.execute(
            '''INSERT INTO company_settings (company_name, company_address, company_phone, company_email, updated_at)
               VALUES (%s, %s, %s, %s, %s)''',
            (settings.get('company_name', ''), settings.get('company_address', ''),
             settings.get('company_phone', ''), settings.get('company_email', ''),
             settings.get('updated_at', ''))
        )

    pg_conn.commit()
    pg_conn.close()
    sqlite_conn.close()
    print("Migration complete!")


if __name__ == '__main__':
    migrate()
