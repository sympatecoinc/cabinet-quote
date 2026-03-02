"""
Cabinet Quoter - Flask Application
Migrated from Streamlit with EmailUI styling
"""

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, request, jsonify, session, Response, redirect, url_for
import os
import json
import csv
import io
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import uuid
from ai_assistant import process_command as ai_process_command

# ReportLab imports for PDF generation
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.pdfgen import canvas

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'cabinet-quoter-secret-key-change-in-production')

# ============================================================================
# DATABASE MANAGER (SQLite local / Cloud SQL PostgreSQL)
# ============================================================================

import sqlite3

class DatabaseManager:
    def __init__(self):
        self.connection_status = "not_initialized"
        self.connection_message = ""
        # Detect PostgreSQL mode
        self.use_postgres = bool(os.environ.get('CLOUD_SQL_CONNECTION_NAME') or os.environ.get('DB_HOST'))
        if self.use_postgres:
            self._pg_pool = None
        else:
            self.db_path = os.path.join(os.path.dirname(__file__), 'cabinet_quoter.db')
        self.initialize_connection()

    def initialize_connection(self):
        try:
            if self.use_postgres:
                from google.cloud.sql.connector import Connector
                connector = Connector()
                conn_name = os.environ.get('CLOUD_SQL_CONNECTION_NAME', '')
                db_user = os.environ.get('DB_USER', 'postgres')
                db_pass = os.environ.get('DB_PASS', '')
                db_name = os.environ.get('DB_NAME', 'cabinet_quoter')

                def getconn():
                    return connector.connect(
                        conn_name,
                        "pg8000",
                        user=db_user,
                        password=db_pass,
                        db=db_name,
                    )

                self._pg_getconn = getconn
                # Test connection
                conn = getconn()
                conn.close()
                self.connection_status = "connected"
                self.connection_message = "Connected to Cloud SQL PostgreSQL"
            else:
                conn = sqlite3.connect(self.db_path)
                conn.close()
                self.connection_status = "connected"
                self.connection_message = "Connected to local SQLite database"
        except Exception as e:
            self.connection_status = "error"
            self.connection_message = f"Database connection failed: {str(e)}"

    def _get_conn(self):
        """Get a database connection."""
        if self.use_postgres:
            return self._pg_getconn()
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn

    def _ph(self, sql: str) -> str:
        """Convert ? placeholders to %s for PostgreSQL."""
        if self.use_postgres:
            return sql.replace('?', '%s')
        return sql

    def _row_to_dict(self, cursor, row) -> Dict:
        """Convert a database row to a dictionary."""
        if self.use_postgres:
            columns = [desc[0] for desc in cursor.description]
            return dict(zip(columns, row))
        else:
            return dict(row)

    def _rows_to_dicts(self, cursor, rows) -> List[Dict]:
        """Convert multiple database rows to a list of dictionaries."""
        if self.use_postgres:
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in rows]
        else:
            return [dict(row) for row in rows]

    def _insert_returning_id(self, conn, cursor, sql: str, params: tuple) -> Optional[int]:
        """Execute an INSERT and return the new row's id."""
        if self.use_postgres:
            sql = self._ph(sql)
            # Append RETURNING id
            sql = sql.rstrip().rstrip(';') + ' RETURNING id'
            cursor.execute(sql, params)
            row = cursor.fetchone()
            conn.commit()
            return row[0] if row else None
        else:
            cursor.execute(sql, params)
            row_id = cursor.lastrowid
            conn.commit()
            return row_id

    def is_connected(self) -> bool:
        return self.connection_status == "connected"

    def save_project(self, project_name: str, cabinets: List[Dict], calc_params: Dict) -> Optional[int]:
        if not self.is_connected():
            return None
        try:
            if cabinets is None:
                cabinets = []
            conn = self._get_conn()
            cursor = conn.cursor()
            sql = '''
                INSERT INTO projects (name, cabinets, calc_params, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
            '''
            project_id = self._insert_returning_id(conn, cursor, sql, (
                project_name.strip(),
                json.dumps(cabinets),
                json.dumps(calc_params),
                datetime.now().isoformat(),
                datetime.now().isoformat()
            ))
            conn.close()
            return project_id
        except Exception as e:
            print(f"Error saving project: {e}")
            return None

    def load_project(self, project_id: int) -> Optional[Dict]:
        if not self.is_connected():
            return None
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(self._ph('SELECT * FROM projects WHERE id = ?'), (project_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return None
            row = self._row_to_dict(cursor, row)
            conn.close()
            # Parse cabinets - handle double-encoded JSON from migration
            cabinets = json.loads(row['cabinets']) if row['cabinets'] else []
            if isinstance(cabinets, str):
                cabinets = json.loads(cabinets)

            # Parse calc_params - handle double-encoded JSON from migration
            calc_params = json.loads(row['calc_params']) if row['calc_params'] else {}
            if isinstance(calc_params, str):
                calc_params = json.loads(calc_params)

            return {
                'id': row['id'],
                'name': row['name'],
                'cabinets': cabinets,
                'calc_params': calc_params,
                'created_at': row['created_at'],
                'updated_at': row['updated_at']
            }
        except Exception as e:
            print(f"Error loading project: {e}")
            return None

    def get_all_projects(self) -> List[Dict]:
        if not self.is_connected():
            return []
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute('SELECT id, name, created_at, updated_at FROM projects ORDER BY updated_at DESC')
            rows = cursor.fetchall()
            result = self._rows_to_dicts(cursor, rows)
            conn.close()
            return result
        except Exception as e:
            print(f"Error fetching projects: {e}")
            return []

    def update_project(self, project_id: int, project_name: str, cabinets: List[Dict], calc_params: Dict) -> bool:
        if not self.is_connected():
            return False
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(self._ph('''
                UPDATE projects SET name = ?, cabinets = ?, calc_params = ?, updated_at = ?
                WHERE id = ?
            '''), (
                project_name,
                json.dumps(cabinets),
                json.dumps(calc_params),
                datetime.now().isoformat(),
                project_id
            ))
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0
        except Exception as e:
            print(f"Error updating project: {e}")
            return False

    def delete_project(self, project_id: int) -> bool:
        if not self.is_connected():
            return False
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(self._ph('DELETE FROM projects WHERE id = ?'), (project_id,))
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0
        except Exception as e:
            print(f"Error deleting project: {e}")
            return False

    # =========================================================================
    # KITCHEN TEMPLATE METHODS
    # =========================================================================

    def save_kitchen_template(self, name: str, description: str, cabinets: List[Dict], project_id: Optional[int] = None, is_global: bool = False) -> Optional[int]:
        """Save a new kitchen template."""
        if not self.is_connected():
            return None
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            sql = '''
                INSERT INTO kitchen_templates (name, description, cabinets, project_id, is_global, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            '''
            template_id = self._insert_returning_id(conn, cursor, sql, (
                name.strip(),
                description.strip() if description else '',
                json.dumps(cabinets),
                project_id,
                1 if is_global else 0,
                datetime.now().isoformat(),
                datetime.now().isoformat()
            ))
            conn.close()
            return template_id
        except Exception as e:
            print(f"Error saving kitchen template: {e}")
            return None

    def get_all_kitchen_templates(self, project_id: Optional[int] = None) -> List[Dict]:
        """Get kitchen templates - global ones plus project-specific if project_id provided."""
        if not self.is_connected():
            return []
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            templates = []

            # Get global templates
            cursor.execute('SELECT * FROM kitchen_templates WHERE is_global = 1')
            rows = cursor.fetchall()
            for row in rows:
                templates.append(self._format_template(self._row_to_dict(cursor, row)))

            # Get project-specific templates if project_id provided
            if project_id:
                cursor.execute(self._ph('SELECT * FROM kitchen_templates WHERE project_id = ? AND is_global = 0'), (project_id,))
                rows = cursor.fetchall()
                for row in rows:
                    templates.append(self._format_template(self._row_to_dict(cursor, row)))

            # Also get templates with no project_id and is_global=False (legacy templates - treat as global)
            cursor.execute('SELECT * FROM kitchen_templates WHERE project_id IS NULL AND is_global = 0')
            rows = cursor.fetchall()
            for row in rows:
                formatted = self._format_template(self._row_to_dict(cursor, row))
                formatted['is_global'] = True  # Treat legacy as global
                templates.append(formatted)

            conn.close()
            # Sort by name
            templates.sort(key=lambda x: x['name'])
            return templates
        except Exception as e:
            print(f"Error fetching kitchen templates: {e}")
            return []

    def _format_template(self, t: Dict) -> Dict:
        """Format a template record from database."""
        cabinets = json.loads(t['cabinets']) if isinstance(t['cabinets'], str) else t['cabinets']
        return {
            'id': t['id'],
            'name': t['name'],
            'description': t.get('description', ''),
            'cabinets': cabinets,
            'cabinet_count': len(cabinets),
            'project_id': t.get('project_id'),
            'is_global': bool(t.get('is_global', 0)),
            'created_at': t['created_at'],
            'updated_at': t['updated_at']
        }

    def get_kitchen_template(self, template_id: int) -> Optional[Dict]:
        """Get a single kitchen template by ID."""
        if not self.is_connected():
            return None
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(self._ph('SELECT * FROM kitchen_templates WHERE id = ?'), (template_id,))
            row = cursor.fetchone()
            conn.close()
            if row:
                return self._format_template(self._row_to_dict(cursor, row))
            return None
        except Exception as e:
            print(f"Error loading kitchen template: {e}")
            return None

    def update_kitchen_template(self, template_id: int, name: str, description: str, cabinets: List[Dict], is_global: Optional[bool] = None) -> bool:
        """Update an existing kitchen template."""
        if not self.is_connected():
            return False
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            if is_global is not None:
                cursor.execute(self._ph('''
                    UPDATE kitchen_templates SET name = ?, description = ?, cabinets = ?, is_global = ?, updated_at = ?
                    WHERE id = ?
                '''), (name.strip(), description.strip() if description else '', json.dumps(cabinets), 1 if is_global else 0, datetime.now().isoformat(), template_id))
            else:
                cursor.execute(self._ph('''
                    UPDATE kitchen_templates SET name = ?, description = ?, cabinets = ?, updated_at = ?
                    WHERE id = ?
                '''), (name.strip(), description.strip() if description else '', json.dumps(cabinets), datetime.now().isoformat(), template_id))
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0
        except Exception as e:
            print(f"Error updating kitchen template: {e}")
            return False

    def set_template_global(self, template_id: int, is_global: bool) -> bool:
        """Set whether a template is global."""
        if not self.is_connected():
            return False
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            if is_global:
                cursor.execute(self._ph('UPDATE kitchen_templates SET is_global = 1, project_id = NULL, updated_at = ? WHERE id = ?'),
                             (datetime.now().isoformat(), template_id))
            else:
                cursor.execute(self._ph('UPDATE kitchen_templates SET is_global = 0, updated_at = ? WHERE id = ?'),
                             (datetime.now().isoformat(), template_id))
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0
        except Exception as e:
            print(f"Error setting template global status: {e}")
            return False

    def delete_kitchen_template(self, template_id: int) -> bool:
        """Delete a kitchen template."""
        if not self.is_connected():
            return False
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(self._ph('DELETE FROM kitchen_templates WHERE id = ?'), (template_id,))
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0
        except Exception as e:
            print(f"Error deleting kitchen template: {e}")
            return False

    def mark_all_templates_global(self) -> bool:
        """Mark all existing templates as global (for migration)."""
        if not self.is_connected():
            return False
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(self._ph('UPDATE kitchen_templates SET is_global = 1, updated_at = ? WHERE is_global IS NULL'),
                         (datetime.now().isoformat(),))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Error marking templates as global: {e}")
            return False

    # =========================================================================
    # STANDARD CABINET METHODS
    # =========================================================================

    def save_standard_cabinet(self, cabinet_data: Dict, project_id: Optional[int] = None, is_global: bool = False) -> Optional[int]:
        """Save a new standard cabinet preset."""
        if not self.is_connected():
            return None
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            sql = '''
                INSERT INTO standard_cabinets (name, code, type, width, height, depth,
                    has_doors, num_doors, has_drawers, num_drawers, has_shelves, num_shelves,
                    has_false_drawers, num_false_drawers, has_dividers, num_dividers,
                    has_pullout_shelves, num_pullout_shelves, use_axial_drawers, edgebanding_type,
                    project_id, is_global, panel_sides, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            '''
            cabinet_id = self._insert_returning_id(conn, cursor, sql, (
                cabinet_data.get('name', '').strip(),
                cabinet_data.get('code', '').strip(),
                cabinet_data.get('type', 'Base Cabinets'),
                cabinet_data.get('width', 24),
                cabinet_data.get('height', 34.5),
                cabinet_data.get('depth', 24),
                1 if cabinet_data.get('has_doors', False) else 0,
                cabinet_data.get('num_doors', 0),
                1 if cabinet_data.get('has_drawers', False) else 0,
                cabinet_data.get('num_drawers', 0),
                1 if cabinet_data.get('has_shelves', False) else 0,
                cabinet_data.get('num_shelves', 0),
                1 if cabinet_data.get('has_false_drawers', False) else 0,
                cabinet_data.get('num_false_drawers', 0),
                1 if cabinet_data.get('has_dividers', False) else 0,
                cabinet_data.get('num_dividers', 0),
                1 if cabinet_data.get('has_pullout_shelves', False) else 0,
                cabinet_data.get('num_pullout_shelves', 0),
                1 if cabinet_data.get('use_axial_drawers', True) else 0,
                cabinet_data.get('edgebanding_type', '1.0mm PVC'),
                project_id,
                1 if is_global else 0,
                cabinet_data.get('panel_sides', 1),
                datetime.now().isoformat(),
                datetime.now().isoformat()
            ))
            conn.close()
            return cabinet_id
        except Exception as e:
            print(f"Error saving standard cabinet: {e}")
            return None

    def get_all_standard_cabinets(self, project_id: Optional[int] = None) -> List[Dict]:
        """Get standard cabinets - global ones plus project-specific if project_id provided."""
        if not self.is_connected():
            return []
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cabinets = []

            # Get global cabinets
            cursor.execute('SELECT * FROM standard_cabinets WHERE is_global = 1')
            rows = cursor.fetchall()
            for row in rows:
                cabinets.append(self._format_standard_cabinet(self._row_to_dict(cursor, row)))

            # Get project-specific cabinets if project_id provided
            if project_id:
                cursor.execute(self._ph('SELECT * FROM standard_cabinets WHERE project_id = ? AND is_global = 0'), (project_id,))
                rows = cursor.fetchall()
                for row in rows:
                    cabinets.append(self._format_standard_cabinet(self._row_to_dict(cursor, row)))

            # Also get cabinets with no project_id and is_global=False (legacy - treat as global)
            cursor.execute('SELECT * FROM standard_cabinets WHERE project_id IS NULL AND is_global = 0')
            rows = cursor.fetchall()
            for row in rows:
                formatted = self._format_standard_cabinet(self._row_to_dict(cursor, row))
                formatted['is_global'] = True  # Treat legacy as global
                cabinets.append(formatted)

            conn.close()
            # Sort by name
            cabinets.sort(key=lambda x: x['name'])
            return cabinets
        except Exception as e:
            print(f"Error fetching standard cabinets: {e}")
            return []

    def _format_standard_cabinet(self, c: Dict) -> Dict:
        """Format a standard cabinet record from database."""
        # Convert integers to booleans
        c['has_doors'] = bool(c.get('has_doors', 0))
        c['has_drawers'] = bool(c.get('has_drawers', 0))
        c['has_shelves'] = bool(c.get('has_shelves', 0))
        c['has_false_drawers'] = bool(c.get('has_false_drawers', 0))
        c['has_dividers'] = bool(c.get('has_dividers', 0))
        c['has_pullout_shelves'] = bool(c.get('has_pullout_shelves', 0))
        c['use_axial_drawers'] = bool(c.get('use_axial_drawers', 1))
        c['is_global'] = bool(c.get('is_global', 0))
        c['project_id'] = c.get('project_id')
        c['panel_sides'] = c.get('panel_sides', 1)
        return c

    def get_standard_cabinet(self, cabinet_id: int) -> Optional[Dict]:
        """Get a single standard cabinet by ID."""
        if not self.is_connected():
            return None
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(self._ph('SELECT * FROM standard_cabinets WHERE id = ?'), (cabinet_id,))
            row = cursor.fetchone()
            conn.close()
            if row:
                return self._format_standard_cabinet(self._row_to_dict(cursor, row))
            return None
        except Exception as e:
            print(f"Error loading standard cabinet: {e}")
            return None

    def update_standard_cabinet(self, cabinet_id: int, cabinet_data: Dict, is_global: Optional[bool] = None) -> bool:
        """Update an existing standard cabinet preset."""
        if not self.is_connected():
            return False
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            params = [
                cabinet_data.get('name', '').strip(),
                cabinet_data.get('code', '').strip(),
                cabinet_data.get('type', 'Base Cabinets'),
                cabinet_data.get('width', 24),
                cabinet_data.get('height', 34.5),
                cabinet_data.get('depth', 24),
                1 if cabinet_data.get('has_doors', False) else 0,
                cabinet_data.get('num_doors', 0),
                1 if cabinet_data.get('has_drawers', False) else 0,
                cabinet_data.get('num_drawers', 0),
                1 if cabinet_data.get('has_shelves', False) else 0,
                cabinet_data.get('num_shelves', 0),
                1 if cabinet_data.get('has_false_drawers', False) else 0,
                cabinet_data.get('num_false_drawers', 0),
                1 if cabinet_data.get('has_dividers', False) else 0,
                cabinet_data.get('num_dividers', 0),
                1 if cabinet_data.get('has_pullout_shelves', False) else 0,
                cabinet_data.get('num_pullout_shelves', 0),
                1 if cabinet_data.get('use_axial_drawers', True) else 0,
                cabinet_data.get('edgebanding_type', '1.0mm PVC'),
                cabinet_data.get('panel_sides', 1),
                datetime.now().isoformat()
            ]

            if is_global is not None:
                params.append(1 if is_global else 0)
                params.append(cabinet_id)
                cursor.execute(self._ph('''
                    UPDATE standard_cabinets SET name = ?, code = ?, type = ?, width = ?, height = ?, depth = ?,
                        has_doors = ?, num_doors = ?, has_drawers = ?, num_drawers = ?, has_shelves = ?, num_shelves = ?,
                        has_false_drawers = ?, num_false_drawers = ?, has_dividers = ?, num_dividers = ?,
                        has_pullout_shelves = ?, num_pullout_shelves = ?, use_axial_drawers = ?, edgebanding_type = ?,
                        panel_sides = ?, updated_at = ?, is_global = ?
                    WHERE id = ?
                '''), params)
            else:
                params.append(cabinet_id)
                cursor.execute(self._ph('''
                    UPDATE standard_cabinets SET name = ?, code = ?, type = ?, width = ?, height = ?, depth = ?,
                        has_doors = ?, num_doors = ?, has_drawers = ?, num_drawers = ?, has_shelves = ?, num_shelves = ?,
                        has_false_drawers = ?, num_false_drawers = ?, has_dividers = ?, num_dividers = ?,
                        has_pullout_shelves = ?, num_pullout_shelves = ?, use_axial_drawers = ?, edgebanding_type = ?,
                        panel_sides = ?, updated_at = ?
                    WHERE id = ?
                '''), params)
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0
        except Exception as e:
            print(f"Error updating standard cabinet: {e}")
            return False

    def set_standard_cabinet_global(self, cabinet_id: int, is_global: bool) -> bool:
        """Set whether a standard cabinet is global."""
        if not self.is_connected():
            return False
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            if is_global:
                cursor.execute(self._ph('UPDATE standard_cabinets SET is_global = 1, project_id = NULL, updated_at = ? WHERE id = ?'),
                             (datetime.now().isoformat(), cabinet_id))
            else:
                cursor.execute(self._ph('UPDATE standard_cabinets SET is_global = 0, updated_at = ? WHERE id = ?'),
                             (datetime.now().isoformat(), cabinet_id))
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0
        except Exception as e:
            print(f"Error setting standard cabinet global status: {e}")
            return False

    def mark_all_standard_cabinets_global(self) -> bool:
        """Mark all existing standard cabinets as global (for migration)."""
        if not self.is_connected():
            return False
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(self._ph('UPDATE standard_cabinets SET is_global = 1, updated_at = ? WHERE is_global IS NULL'),
                         (datetime.now().isoformat(),))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Error marking standard cabinets as global: {e}")
            return False

    def delete_standard_cabinet(self, cabinet_id: int) -> bool:
        """Delete a standard cabinet preset."""
        if not self.is_connected():
            return False
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(self._ph('DELETE FROM standard_cabinets WHERE id = ?'), (cabinet_id,))
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0
        except Exception as e:
            print(f"Error deleting standard cabinet: {e}")
            return False

    # =========================================================================
    # PRICING RULES METHODS
    # =========================================================================

    def save_pricing_rule(self, rule_data: Dict) -> Optional[int]:
        """Save a new pricing rule preset."""
        if not self.is_connected():
            return None
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            sql = '''
                INSERT INTO pricing_rules (name, description, markup_primary, markup_back,
                    markup_door_drawer, markup_drawer_material, markup_hardware, markup_edgebanding,
                    material_usage_buffer, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            '''
            rule_id = self._insert_returning_id(conn, cursor, sql, (
                rule_data.get('name', '').strip(),
                rule_data.get('description', '').strip(),
                rule_data.get('markup_primary', 0),
                rule_data.get('markup_back', 0),
                rule_data.get('markup_door_drawer', 0),
                rule_data.get('markup_drawer_material', 0),
                rule_data.get('markup_hardware', 0),
                rule_data.get('markup_edgebanding', 0),
                rule_data.get('material_usage_buffer', 0),
                datetime.now().isoformat(),
                datetime.now().isoformat()
            ))
            conn.close()
            return rule_id
        except Exception as e:
            print(f"Error saving pricing rule: {e}")
            return None

    def get_all_pricing_rules(self) -> List[Dict]:
        """Get all pricing rule presets."""
        if not self.is_connected():
            return []
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM pricing_rules ORDER BY name')
            rows = cursor.fetchall()
            result = self._rows_to_dicts(cursor, rows)
            conn.close()
            return result
        except Exception as e:
            print(f"Error fetching pricing rules: {e}")
            return []

    def get_pricing_rule(self, rule_id: int) -> Optional[Dict]:
        """Get a single pricing rule by ID."""
        if not self.is_connected():
            return None
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(self._ph('SELECT * FROM pricing_rules WHERE id = ?'), (rule_id,))
            row = cursor.fetchone()
            conn.close()
            if row:
                return self._row_to_dict(cursor, row)
            return None
        except Exception as e:
            print(f"Error loading pricing rule: {e}")
            return None

    def update_pricing_rule(self, rule_id: int, rule_data: Dict) -> bool:
        """Update an existing pricing rule."""
        if not self.is_connected():
            return False
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(self._ph('''
                UPDATE pricing_rules SET name = ?, description = ?, markup_primary = ?, markup_back = ?,
                    markup_door_drawer = ?, markup_drawer_material = ?, markup_hardware = ?, markup_edgebanding = ?,
                    material_usage_buffer = ?, updated_at = ?
                WHERE id = ?
            '''), (
                rule_data.get('name', '').strip(),
                rule_data.get('description', '').strip(),
                rule_data.get('markup_primary', 0),
                rule_data.get('markup_back', 0),
                rule_data.get('markup_door_drawer', 0),
                rule_data.get('markup_drawer_material', 0),
                rule_data.get('markup_hardware', 0),
                rule_data.get('markup_edgebanding', 0),
                rule_data.get('material_usage_buffer', 0),
                datetime.now().isoformat(),
                rule_id
            ))
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0
        except Exception as e:
            print(f"Error updating pricing rule: {e}")
            return False

    def delete_pricing_rule(self, rule_id: int) -> bool:
        """Delete a pricing rule."""
        if not self.is_connected():
            return False
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(self._ph('DELETE FROM pricing_rules WHERE id = ?'), (rule_id,))
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0
        except Exception as e:
            print(f"Error deleting pricing rule: {e}")
            return False

    # =========================================================================
    # COMPANY SETTINGS METHODS
    # =========================================================================

    def get_company_settings(self) -> Optional[Dict]:
        """Get the company settings."""
        if self.use_postgres:
            try:
                conn = self._get_conn()
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM company_settings ORDER BY id LIMIT 1')
                row = cursor.fetchone()
                conn.close()
                if row:
                    return self._row_to_dict(cursor, row)
                return None
            except Exception as e:
                print(f"Error fetching company settings: {e}")
                return None
        else:
            settings_file = os.path.join(os.path.dirname(__file__), 'company_settings.json')
            try:
                if os.path.exists(settings_file):
                    with open(settings_file, 'r') as f:
                        return json.load(f)
                return None
            except Exception as e:
                print(f"Error fetching company settings: {e}")
                return None

    def save_company_settings(self, settings: Dict) -> bool:
        """Save company settings."""
        data = {
            'company_name': settings.get('company_name', ''),
            'company_address': settings.get('company_address', ''),
            'company_phone': settings.get('company_phone', ''),
            'company_email': settings.get('company_email', ''),
            'updated_at': datetime.now().isoformat()
        }
        if self.use_postgres:
            try:
                conn = self._get_conn()
                cursor = conn.cursor()
                # Upsert: update if exists, insert if not
                cursor.execute('SELECT id FROM company_settings LIMIT 1')
                row = cursor.fetchone()
                if row:
                    cursor.execute(self._ph('''
                        UPDATE company_settings SET company_name = ?, company_address = ?,
                            company_phone = ?, company_email = ?, updated_at = ?
                        WHERE id = ?
                    '''), (data['company_name'], data['company_address'], data['company_phone'],
                           data['company_email'], data['updated_at'], row[0]))
                else:
                    cursor.execute(self._ph('''
                        INSERT INTO company_settings (company_name, company_address, company_phone, company_email, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                    '''), (data['company_name'], data['company_address'], data['company_phone'],
                           data['company_email'], data['updated_at']))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                print(f"Error saving company settings: {e}")
                return False
        else:
            settings_file = os.path.join(os.path.dirname(__file__), 'company_settings.json')
            try:
                with open(settings_file, 'w') as f:
                    json.dump(data, f, indent=2)
                return True
            except Exception as e:
                print(f"Error saving company settings: {e}")
                return False

    # =========================================================================
    # APARTMENT COMPLEX METHODS
    # =========================================================================

    def save_apartment_complex(self, name: str, data: Dict, calc_params: Dict) -> Optional[int]:
        """Save a new apartment complex project."""
        if not self.is_connected():
            return None
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            sql = '''
                INSERT INTO projects (name, project_type, cabinets, data, calc_params, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            '''
            project_id = self._insert_returning_id(conn, cursor, sql, (
                name.strip(),
                'apartment_complex',
                json.dumps([]),
                json.dumps(data),
                json.dumps(calc_params),
                datetime.now().isoformat(),
                datetime.now().isoformat()
            ))
            conn.close()
            return project_id
        except Exception as e:
            print(f"Error saving apartment complex: {e}")
            return None

    def load_project_with_type(self, project_id: int) -> Optional[Dict]:
        """Load a project with type awareness (standard or apartment complex)."""
        if not self.is_connected():
            return None
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(self._ph('SELECT * FROM projects WHERE id = ?'), (project_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return None
            project = self._row_to_dict(cursor, row)
            conn.close()
            project_type = project.get('project_type', 'standard')

            # Parse calc_params - handle double-encoded JSON from migration
            calc_params = json.loads(project['calc_params']) if project['calc_params'] else {}
            if isinstance(calc_params, str):
                calc_params = json.loads(calc_params)

            base_project = {
                'id': project['id'],
                'name': project['name'],
                'project_type': project_type,
                'calc_params': calc_params,
                'created_at': project['created_at'],
                'updated_at': project['updated_at']
            }

            if project_type == 'apartment_complex':
                data = json.loads(project['data']) if project.get('data') else {'units': []}
                if isinstance(data, str):
                    data = json.loads(data)
                base_project['data'] = data
            else:
                cabinets = json.loads(project['cabinets']) if project['cabinets'] else []
                if isinstance(cabinets, str):
                    cabinets = json.loads(cabinets)
                base_project['cabinets'] = cabinets

            return base_project
        except Exception as e:
            print(f"Error loading project: {e}")
            return None

    def update_apartment_complex(self, project_id: int, name: str, data: Dict, calc_params: Dict) -> bool:
        """Update an apartment complex project."""
        if not self.is_connected():
            return False
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(self._ph('''
                UPDATE projects SET name = ?, data = ?, calc_params = ?, updated_at = ?
                WHERE id = ?
            '''), (
                name,
                json.dumps(data),
                json.dumps(calc_params),
                datetime.now().isoformat(),
                project_id
            ))
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0
        except Exception as e:
            print(f"Error updating apartment complex: {e}")
            return False

    def get_all_projects_with_type(self) -> List[Dict]:
        """Get all projects including type information."""
        if not self.is_connected():
            return []
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute('SELECT id, name, project_type, created_at, updated_at FROM projects ORDER BY updated_at DESC')
            rows = cursor.fetchall()
            all_rows = self._rows_to_dicts(cursor, rows)
            conn.close()
            projects = []
            for p in all_rows:
                projects.append({
                    'id': p['id'],
                    'name': p['name'],
                    'project_type': p.get('project_type', 'standard'),
                    'created_at': p['created_at'],
                    'updated_at': p['updated_at']
                })
            return projects
        except Exception as e:
            print(f"Error fetching projects: {e}")
            return []

db_manager = DatabaseManager()

# ============================================================================
# DEFAULT CALCULATION PARAMETERS
# ============================================================================

DEFAULT_CALC_PARAMS = {
    'primary_substrate_thickness': 0.76,
    'back_panel_thickness': 0.25,
    'drawer_material_thickness': 0.63,
    'door_drawer_fronts_thickness': 0.76,
    'edge_adjustment': 0.0788,
    'bottom_top_clearance': 0.48,
    'shelf_clearance': 0.48,
    'shelf_setback': 0.5,
    'door_clearance': 0.125,
    'door_height_adjustment': 0.125,
    'toe_kick_height': 4.5,
    'toe_kick_inset': 4.0,
    'drawer_front_height': 7.09,
    'drawer_clearance': 0.125,
    'drawer_gap': 0.125,
    'tall_door_clearance': 0.125,
    'tall_door_gap': 0.0625,
    'nailer_width': 3.78,
    'stretcher_width': 3.78,
    'drawer_cabinet_toe_kick_height': 3.78,
    'drawer_cabinet_nailer_width': 3.78,
    'material_costs': {
        "Primary Substrate": 1.50,
        "Back Panel": 0.85,
        "Drawer Material": 1.25,
        "Door/Drawer Fronts": 1.75
    },
    'hardware_costs': {
        "Hinge": 2.50,
        "Handle": 3.75,
        "Drawer Pull": 4.25,
        "Shelf Pin": 0.15,
        "Drawer Slide": 12.50,
        "Heavy Duty Drawer Slide": 15.00,
        "Divider Bracket": 1.25,
        "AXIAL Drawer System": 20.00
    },
    'edgebanding_costs': {
        "0.5mm PVC": 0.15,
        "1.0mm PVC": 0.25,
        "2.0mm PVC": 0.35
    },
    'drawer_cabinet_toe_kick_height': 3.78,
    'drawer_cabinet_nailer_width': 3.78,
    'nailer_width': 3.78,
    'stretcher_width': 3.78,
    # Markup percentages (as decimals, e.g., 0.25 = 25%)
    'markup_primary': 0.0,
    'markup_back': 0.0,
    'markup_door_drawer': 0.0,
    'markup_drawer_material': 0.0,
    'markup_hardware': 0.0,
    'markup_edgebanding': 0.0,
    'material_usage_buffer': 0,  # Percentage to add to material costs (e.g., 15 = 15%)
    # Customer information (per-project)
    'customer_name': '',
    'customer_address': '',
    'customer_phone': '',
    'customer_email': '',
    'due_date': ''
}

# ============================================================================
# CALCULATION FUNCTIONS
# ============================================================================

def calculate_sq_feet(length: float, height: float) -> float:
    return (length * height) / 144

def calculate_edgebanding(component_name: str, length: float, height: float, cabinet_type: str = "") -> float:
    """Calculate linear feet of edgebanding needed for a component."""
    edges_to_band = 0

    # Appliance Panel - band all 4 edges
    if component_name.startswith("Panel"):
        edges_to_band = 4
        return ((length * 2) + (height * 2)) / 12
    elif "Side panel" in component_name:
        edges_to_band = 1
        return (height / 12)
    elif "Bottom" in component_name or "Top" in component_name:
        edges_to_band = 1
        return (length / 12)
    elif "Back Panel" in component_name:
        edges_to_band = 0
        return 0
    elif "Shelf" in component_name:
        edges_to_band = 1
        return (length / 12)
    elif "Door" in component_name:
        edges_to_band = 4
        return ((length * 2) + (height * 2)) / 12
    elif "Drawer Front" in component_name:
        edges_to_band = 4
        return ((length * 2) + (height * 2)) / 12
    elif "Drawer Back" in component_name or "Drawer Bottom" in component_name:
        edges_to_band = 0
        return 0
    elif "Toe Kick" in component_name:
        edges_to_band = 1
        return (length / 12)
    elif "Nailer" in component_name or "Stretcher" in component_name:
        edges_to_band = 0
        return 0
    else:
        return 0

def calculate_cabinet_components(
    cabinet_type: str,
    width: float,
    height: float,
    depth: float,
    has_shelves: bool,
    num_shelves: int,
    has_drawers: bool,
    num_drawers: int,
    has_false_drawers: bool,
    num_false_drawers: int,
    has_dividers: bool,
    num_dividers: int,
    has_pullout_shelves: bool,
    num_pullout_shelves: int,
    use_axial_drawers: bool,
    has_doors: bool,
    num_doors: int,
    calc_params: Dict,
    panel_sides: int = 0
) -> Dict:
    """Calculate all component dimensions for a cabinet."""
    components = {}

    # Special handling for Appliance Panel
    if cabinet_type == "Appliance Panel":
        # Panel 1: Always present (height x depth)
        components["Panel 1"] = {
            "length": depth,
            "height": height,
            "qty": 1,
            "material": "Primary Substrate"
        }
        # Panel 2: Present for 2 or 3 sided (height x width)
        if panel_sides >= 2:
            components["Panel 2"] = {
                "length": width,
                "height": height,
                "qty": 1,
                "material": "Primary Substrate"
            }
        # Panel 3: Present for 3 sided (height x depth)
        if panel_sides >= 3:
            components["Panel 3"] = {
                "length": depth,
                "height": height,
                "qty": 1,
                "material": "Primary Substrate"
            }
        return components

    primary_thickness = calc_params.get('primary_substrate_thickness', 0.76)
    back_thickness = calc_params.get('back_panel_thickness', 0.25)
    drawer_thickness = calc_params.get('drawer_material_thickness', 0.63)
    door_fronts_thickness = calc_params.get('door_drawer_fronts_thickness', 0.76)
    edge_adj = calc_params.get('edge_adjustment', 0.0788)
    toe_kick = calc_params.get('toe_kick_height', 4.5)
    drawer_front_h = calc_params.get('drawer_front_height', 7.09)
    nailer_width = calc_params.get('nailer_width', 3.78)
    stretcher_width = calc_params.get('stretcher_width', 3.78)
    shelf_setback = calc_params.get('shelf_setback', 0.5)
    door_clearance = calc_params.get('door_clearance', 0.125)
    door_height_adj = calc_params.get('door_height_adjustment', 0.125)
    drawer_cabinet_toe_kick = calc_params.get('drawer_cabinet_toe_kick_height', 3.78)
    drawer_cabinet_nailer = calc_params.get('drawer_cabinet_nailer_width', 3.78)
    drawer_gap = calc_params.get('drawer_gap', 0.125)

    # Side Panels (always 2)
    if cabinet_type == "Wall Cabinets":
        side_height = height
        side_depth = depth
    elif cabinet_type == "Drawer Cabinets":
        side_height = height
        side_depth = depth
    else:
        side_height = height
        side_depth = depth

    components["Side panel (L)"] = {
        "length": side_depth,
        "height": side_height,
        "qty": 1,
        "material": "Primary Substrate"
    }
    components["Side panel (R)"] = {
        "length": side_depth,
        "height": side_height,
        "qty": 1,
        "material": "Primary Substrate"
    }

    # Back Panel
    if cabinet_type == "Wall Cabinets":
        back_width = width - (primary_thickness * 2)
        back_height = height - (primary_thickness * 2)
    elif cabinet_type == "Drawer Cabinets":
        back_width = width - (primary_thickness * 2)
        back_height = height - drawer_cabinet_toe_kick - primary_thickness
    else:
        back_width = width - (primary_thickness * 2) - edge_adj
        back_height = height - toe_kick - (primary_thickness * 2) - edge_adj

    # Tall cabinets use primary substrate for back panel
    back_material = "Primary Substrate" if cabinet_type == "Tall Cabinets" else "Back Panel"
    components["Back Panel"] = {
        "length": back_width,
        "height": back_height,
        "qty": 1,
        "material": back_material
    }

    # Bottom Panel
    if cabinet_type == "Wall Cabinets":
        bottom_width = width - (primary_thickness * 2)
        bottom_depth = depth - back_thickness
    elif cabinet_type == "Drawer Cabinets":
        bottom_width = width - (primary_thickness * 2)
        bottom_depth = depth - back_thickness
    else:
        bottom_width = width - (primary_thickness * 2)
        bottom_depth = depth - back_thickness

    components["Bottom"] = {
        "length": bottom_width,
        "height": bottom_depth,
        "qty": 1,
        "material": "Primary Substrate"
    }

    # Top Panel (for wall cabinets and some others)
    if cabinet_type in ["Wall Cabinets", "Tall Cabinets"]:
        top_width = width - (primary_thickness * 2)
        top_depth = depth - back_thickness
        components["Top"] = {
            "length": top_width,
            "height": top_depth,
            "qty": 1,
            "material": "Primary Substrate"
        }

    # Toe Kick (for base and drawer cabinets)
    if cabinet_type in ["Base Cabinets", "Open Base Cabinets", "Drawer Cabinets", "Sink Base Cabinets", "Pull Out Trashcan"]:
        if cabinet_type == "Drawer Cabinets":
            tk_height = drawer_cabinet_toe_kick
        else:
            tk_height = toe_kick
        components["Toe Kick"] = {
            "length": width - (primary_thickness * 2),
            "height": tk_height,
            "qty": 1,
            "material": "Primary Substrate"
        }

    # Nailer/Stretcher
    if cabinet_type in ["Base Cabinets", "Open Base Cabinets", "Sink Base Cabinets", "Pull Out Trashcan"]:
        components["Nailer"] = {
            "length": width - (primary_thickness * 2),
            "height": nailer_width,
            "qty": 1,
            "material": "Primary Substrate"
        }
        # Sink base cabinets don't get a stretcher
        if cabinet_type in ["Base Cabinets", "Open Base Cabinets", "Pull Out Trashcan"]:
            components["Stretcher"] = {
                "length": width - (primary_thickness * 2),
                "height": stretcher_width,
                "qty": 1,
                "material": "Primary Substrate"
            }
    elif cabinet_type == "Drawer Cabinets":
        components["Nailer"] = {
            "length": width - (primary_thickness * 2),
            "height": drawer_cabinet_nailer,
            "qty": 1,
            "material": "Primary Substrate"
        }

    # Shelves
    if has_shelves and num_shelves > 0:
        shelf_width = width - (primary_thickness * 2)
        shelf_depth = depth - back_thickness - shelf_setback
        components["Shelf"] = {
            "length": shelf_width,
            "height": shelf_depth,
            "qty": num_shelves,
            "material": "Primary Substrate"
        }

    # Doors
    if has_doors and num_doors > 0:
        if num_doors == 1:
            door_width = width - (door_clearance * 2)
        else:
            door_width = (width - (door_clearance * 2) - (door_clearance * (num_doors - 1))) / num_doors

        if cabinet_type == "Wall Cabinets":
            door_height = height - door_height_adj
        elif cabinet_type == "Tall Cabinets":
            door_height = height - door_height_adj
        else:
            if has_drawers and num_drawers > 0:
                door_height = height - toe_kick - door_height_adj - drawer_front_h
            else:
                door_height = height - toe_kick - door_height_adj

        components["Door"] = {
            "length": door_width,
            "height": door_height,
            "qty": num_doors,
            "material": "Door/Drawer Fronts"
        }

    # Drawers (AXIAL System - includes slides and sides, only need fronts, backs, bottoms)
    if has_drawers and num_drawers > 0:
        if cabinet_type == "Drawer Cabinets":
            drawer_front_height = (height - drawer_cabinet_toe_kick) / num_drawers
        else:
            drawer_front_height = drawer_front_h

        drawer_front_width = width - (door_clearance * 2)
        drawer_box_width = width - (primary_thickness * 2) - 1.0
        drawer_box_depth = depth - 2.0

        components["Drawer Front"] = {
            "length": drawer_front_width,
            "height": drawer_front_height,
            "qty": num_drawers,
            "material": "Door/Drawer Fronts"
        }
        components["Drawer Back"] = {
            "length": drawer_box_width,
            "height": 3.937,
            "qty": num_drawers,
            "material": "Drawer Material"
        }
        components["Drawer Bottom"] = {
            "length": drawer_box_width,
            "height": drawer_box_depth,
            "qty": num_drawers,
            "material": "Drawer Material"
        }

    # False Drawers
    if has_false_drawers and num_false_drawers > 0:
        false_drawer_width = width - (door_clearance * 2)
        false_drawer_height = drawer_front_h
        components["False Drawer Front"] = {
            "length": false_drawer_width,
            "height": false_drawer_height,
            "qty": num_false_drawers,
            "material": "Door/Drawer Fronts"
        }

    return components

def calculate_hardware(
    cabinet_type: str,
    has_doors: bool,
    num_doors: int,
    has_drawers: bool,
    num_drawers: int,
    has_shelves: bool,
    num_shelves: int,
    has_false_drawers: bool = False,
    num_false_drawers: int = 0,
    has_dividers: bool = False,
    num_dividers: int = 0,
    has_pullout_shelves: bool = False,
    num_pullout_shelves: int = 0,
    use_axial_drawers: bool = True
) -> Dict[str, int]:
    """Calculate hardware quantities for a cabinet."""
    hardware = {}

    if has_doors and num_doors > 0:
        hardware["Hinge"] = num_doors * 2
        hardware["Handle"] = num_doors

    if has_drawers and num_drawers > 0:
        if use_axial_drawers:
            hardware["AXIAL Drawer System"] = num_drawers
        else:
            hardware["Drawer Slide"] = num_drawers
        hardware["Drawer Pull"] = num_drawers

    if has_shelves and num_shelves > 0:
        hardware["Shelf Pin"] = num_shelves * 4

    if has_pullout_shelves and num_pullout_shelves > 0:
        hardware["Heavy Duty Drawer Slide"] = num_pullout_shelves

    if has_dividers and num_dividers > 0:
        hardware["Divider Bracket"] = num_dividers * 2

    return hardware

def calculate_costs(components: Dict, hardware: Dict, quantity: int, cabinet_type: str, edgebanding_type: str, calc_params: Dict) -> Tuple[float, float, float]:
    """Calculate total costs for materials, hardware, and edgebanding."""
    material_costs = calc_params.get('material_costs', DEFAULT_CALC_PARAMS['material_costs'])
    hardware_costs = calc_params.get('hardware_costs', DEFAULT_CALC_PARAMS['hardware_costs'])
    edgebanding_costs = calc_params.get('edgebanding_costs', DEFAULT_CALC_PARAMS['edgebanding_costs'])

    material_cost = 0.0
    for comp_name, comp_data in components.items():
        sq_feet = calculate_sq_feet(comp_data["length"], comp_data["height"])
        cost_per_sqft = material_costs.get(comp_data["material"], 1.50)
        material_cost += sq_feet * cost_per_sqft * comp_data["qty"]

    hardware_cost = 0.0
    for hw_name, hw_qty in hardware.items():
        cost_per_unit = hardware_costs.get(hw_name, 0)
        hardware_cost += cost_per_unit * hw_qty

    edgebanding_cost = 0.0
    eb_cost_per_ft = edgebanding_costs.get(edgebanding_type, 0.25)
    for comp_name, comp_data in components.items():
        linear_feet = calculate_edgebanding(comp_name, comp_data["length"], comp_data["height"], cabinet_type)
        edgebanding_cost += linear_feet * eb_cost_per_ft * comp_data["qty"]

    return (material_cost * quantity, hardware_cost * quantity, edgebanding_cost * quantity)

def calculate_costs_detailed(components: Dict, hardware: Dict, quantity: int, cabinet_type: str, edgebanding_type: str, calc_params: Dict) -> Dict:
    """Calculate detailed costs breakdown by material type for markup calculations."""
    material_costs = calc_params.get('material_costs', DEFAULT_CALC_PARAMS['material_costs'])
    hardware_costs = calc_params.get('hardware_costs', DEFAULT_CALC_PARAMS['hardware_costs'])
    edgebanding_costs = calc_params.get('edgebanding_costs', DEFAULT_CALC_PARAMS['edgebanding_costs'])

    # Track costs by material type
    cost_by_material = {
        'Primary Substrate': 0.0,
        'Back Panel': 0.0,
        'Door/Drawer Fronts': 0.0,
        'Drawer Material': 0.0
    }

    for comp_name, comp_data in components.items():
        sq_feet = calculate_sq_feet(comp_data["length"], comp_data["height"])
        material_type = comp_data["material"]
        cost_per_sqft = material_costs.get(material_type, 1.50)
        cost = sq_feet * cost_per_sqft * comp_data["qty"]
        if material_type in cost_by_material:
            cost_by_material[material_type] += cost

    hardware_cost = 0.0
    for hw_name, hw_qty in hardware.items():
        cost_per_unit = hardware_costs.get(hw_name, 0)
        hardware_cost += cost_per_unit * hw_qty

    edgebanding_cost = 0.0
    eb_cost_per_ft = edgebanding_costs.get(edgebanding_type, 0.25)
    for comp_name, comp_data in components.items():
        linear_feet = calculate_edgebanding(comp_name, comp_data["length"], comp_data["height"], cabinet_type)
        edgebanding_cost += linear_feet * eb_cost_per_ft * comp_data["qty"]

    return {
        'primary': cost_by_material['Primary Substrate'] * quantity,
        'back': cost_by_material['Back Panel'] * quantity,
        'door_drawer': cost_by_material['Door/Drawer Fronts'] * quantity,
        'drawer_material': cost_by_material['Drawer Material'] * quantity,
        'hardware': hardware_cost * quantity,
        'edgebanding': edgebanding_cost * quantity
    }

def apply_markups(detailed_costs: Dict, calc_params: Dict) -> Dict:
    """Apply markup percentages to detailed costs and return marked up totals."""
    markup_primary = calc_params.get('markup_primary', 0.0)
    markup_back = calc_params.get('markup_back', 0.0)
    markup_door_drawer = calc_params.get('markup_door_drawer', 0.0)
    markup_drawer_material = calc_params.get('markup_drawer_material', 0.0)
    markup_hardware = calc_params.get('markup_hardware', 0.0)
    markup_edgebanding = calc_params.get('markup_edgebanding', 0.0)
    buffer_percent = calc_params.get('material_usage_buffer', 0)

    # Apply buffer percentage to material costs (e.g., 15 = 15% extra)
    buffer_multiplier = 1 + (buffer_percent / 100)

    # Calculate marked up costs
    primary_marked = detailed_costs['primary'] * buffer_multiplier * (1 + markup_primary)
    back_marked = detailed_costs['back'] * buffer_multiplier * (1 + markup_back)
    door_drawer_marked = detailed_costs['door_drawer'] * buffer_multiplier * (1 + markup_door_drawer)
    drawer_material_marked = detailed_costs['drawer_material'] * buffer_multiplier * (1 + markup_drawer_material)
    hardware_marked = detailed_costs['hardware'] * (1 + markup_hardware)
    edgebanding_marked = detailed_costs['edgebanding'] * buffer_multiplier * (1 + markup_edgebanding)

    material_total_marked = primary_marked + back_marked + door_drawer_marked + drawer_material_marked

    return {
        'material': material_total_marked,
        'hardware': hardware_marked,
        'edgebanding': edgebanding_marked,
        'total': material_total_marked + hardware_marked + edgebanding_marked
    }

def parse_cabinet_code(code: str) -> Optional[Dict]:
    """Parse a cabinet code like W1530 or BC24,2 into specifications."""
    import re

    code = code.strip().upper()
    parts = code.split(',')
    cabinet_code = parts[0].strip()
    quantity = int(parts[1].strip()) if len(parts) > 1 else 1

    patterns = {
        'W': ('Wall Cabinets', 12, 30),
        'BC': ('Base Cabinets', 24, 35),
        'DB': ('Drawer Cabinets', 24, 35),
        'TC': ('Tall Cabinets', 24, 96),
        'SB': ('Base Cabinets', 24, 35),
        'SKB': ('Sink Base Cabinets', 24, 34.5),
        'SU': ('Tall Cabinets', 24, 96),
        'PT': ('Pull Out Trashcan', 24, 35),
    }

    for prefix, (cab_type, default_depth, default_height) in patterns.items():
        if cabinet_code.startswith(prefix):
            dims = cabinet_code[len(prefix):]

            if len(dims) == 2:
                width = int(dims)
                height = default_height
            elif len(dims) == 4:
                width = int(dims[:2])
                height = int(dims[2:])
            else:
                return None

            has_doors = cab_type not in ['Drawer Cabinets', 'Pull Out Trashcan']
            num_doors = 2 if width >= 24 and has_doors else (1 if has_doors else 0)
            has_drawers = cab_type in ['Drawer Cabinets', 'Base Cabinets']
            num_drawers = 4 if cab_type == 'Drawer Cabinets' else (1 if cab_type == 'Base Cabinets' else 0)
            has_shelves = cab_type in ['Wall Cabinets', 'Base Cabinets', 'Tall Cabinets']
            num_shelves = 2 if cab_type == 'Tall Cabinets' else 1

            if prefix == 'SU':
                has_doors = False
                num_doors = 0
                has_shelves = True
                num_shelves = 4

            if prefix == 'SKB':
                has_doors = True
                num_doors = 2
                has_drawers = False
                num_drawers = 0

            return {
                'type': cab_type,
                'width': width,
                'height': height,
                'depth': default_depth,
                'has_doors': has_doors,
                'num_doors': num_doors,
                'has_drawers': has_drawers,
                'num_drawers': num_drawers,
                'has_shelves': has_shelves,
                'num_shelves': num_shelves,
                'has_false_drawers': prefix == 'SKB',
                'num_false_drawers': 1 if prefix == 'SKB' else 0,
                'has_dividers': False,
                'num_dividers': 0,
                'has_pullout_shelves': False,
                'num_pullout_shelves': 0,
                'use_axial_drawers': True,
                'edgebanding_type': '1.0mm PVC',
                'quantity': quantity
            }

    return None

# ============================================================================
# SESSION/STATE MANAGEMENT
# ============================================================================

def _decode_json_field(data, default):
    """Decode JSON field, handling double-encoded strings from migration."""
    if isinstance(data, str):
        try:
            decoded = json.loads(data)
            # Handle double-encoded JSON
            if isinstance(decoded, str):
                decoded = json.loads(decoded)
            return decoded
        except (json.JSONDecodeError, TypeError):
            return default
    return data

def get_state():
    """Get current session state with defaults."""
    # Handle cabinets - may be string from corrupted session
    if 'cabinets' not in session:
        session['cabinets'] = []
    else:
        session['cabinets'] = _decode_json_field(session['cabinets'], [])
        # Also check if list items are strings (double-encoded list items)
        if session['cabinets'] and isinstance(session['cabinets'][0], str):
            try:
                session['cabinets'] = [json.loads(c) if isinstance(c, str) else c for c in session['cabinets']]
            except (json.JSONDecodeError, TypeError):
                session['cabinets'] = []

    # Handle calc_params - may be string from corrupted session
    if 'calc_params' not in session:
        session['calc_params'] = DEFAULT_CALC_PARAMS.copy()
    else:
        session['calc_params'] = _decode_json_field(session['calc_params'], DEFAULT_CALC_PARAMS.copy())
        # Merge in any missing default keys (for existing sessions)
        if isinstance(session['calc_params'], dict):
            for key, value in DEFAULT_CALC_PARAMS.items():
                if key not in session['calc_params']:
                    session['calc_params'][key] = value
        else:
            session['calc_params'] = DEFAULT_CALC_PARAMS.copy()

    session.modified = True
    if 'current_project' not in session:
        session['current_project'] = None
    return session

def calculate_cabinet_totals(cabinets: List[Dict], calc_params: Dict) -> Dict:
    """Calculate totals for all cabinets including detailed costs and markups."""
    total_material = 0.0
    total_hardware = 0.0
    total_edgebanding = 0.0

    # Detailed costs for markup calculations
    detailed_totals = {
        'primary': 0.0,
        'back': 0.0,
        'door_drawer': 0.0,
        'drawer_material': 0.0,
        'hardware': 0.0,
        'edgebanding': 0.0
    }

    for cabinet in cabinets:
        # Handle Hardware items differently
        if cabinet.get('type') == 'Hardware':
            hw_cost = cabinet.get('hardware_cost', 0) * cabinet.get('quantity', 1)
            total_hardware += hw_cost
            detailed_totals['hardware'] += hw_cost
        else:
            components = calculate_cabinet_components(
                cabinet['type'], cabinet['width'], cabinet['height'], cabinet['depth'],
                cabinet['has_shelves'], cabinet['num_shelves'],
                cabinet['has_drawers'], cabinet['num_drawers'],
                cabinet.get('has_false_drawers', False), cabinet.get('num_false_drawers', 0),
                cabinet.get('has_dividers', False), cabinet.get('num_dividers', 0),
                cabinet.get('has_pullout_shelves', False), cabinet.get('num_pullout_shelves', 0),
                cabinet.get('use_axial_drawers', True),
                cabinet['has_doors'], cabinet['num_doors'],
                calc_params,
                cabinet.get('panel_sides', 0)
            )
            hardware = calculate_hardware(
                cabinet['type'], cabinet['has_doors'], cabinet['num_doors'],
                cabinet['has_drawers'], cabinet['num_drawers'],
                cabinet['has_shelves'], cabinet['num_shelves'],
                cabinet.get('has_false_drawers', False), cabinet.get('num_false_drawers', 0),
                cabinet.get('has_dividers', False), cabinet.get('num_dividers', 0),
                cabinet.get('has_pullout_shelves', False), cabinet.get('num_pullout_shelves', 0),
                cabinet.get('use_axial_drawers', True)
            )
            mat, hw, eb = calculate_costs(
                components, hardware, cabinet.get('quantity', 1),
                cabinet['type'], cabinet.get('edgebanding_type', '1.0mm PVC'),
                calc_params
            )
            total_material += mat
            total_hardware += hw
            total_edgebanding += eb

            # Get detailed costs for this cabinet
            detailed = calculate_costs_detailed(
                components, hardware, cabinet.get('quantity', 1),
                cabinet['type'], cabinet.get('edgebanding_type', '1.0mm PVC'),
                calc_params
            )
            for key in detailed_totals:
                detailed_totals[key] += detailed[key]

    # Calculate marked up totals
    marked_up = apply_markups(detailed_totals, calc_params)

    return {
        'material': total_material,
        'hardware': total_hardware,
        'edgebanding': total_edgebanding,
        'total': total_material + total_hardware + total_edgebanding,  # Base cost (no markup)
        'marked_up': marked_up  # Costs with markup applied
    }

# ============================================================================
# ROUTES
# ============================================================================

@app.route('/')
def index():
    state = get_state()
    cabinets = state.get('cabinets', [])
    calc_params = state.get('calc_params', DEFAULT_CALC_PARAMS)
    current_project = state.get('current_project')

    # Redirect to apartment complex dashboard if that's the current project type
    if current_project and current_project.get('project_type') == 'apartment_complex':
        return redirect(url_for('complex_page', project_id=current_project['id']))

    # Calculate costs for each cabinet
    cabinet_data = []
    for cab in cabinets:
        # Handle Hardware items differently
        if cab.get('type') == 'Hardware':
            hw_cost = cab.get('hardware_cost', 0) * cab.get('quantity', 1)
            cabinet_data.append({
                **cab,
                'material_cost': 0,
                'hardware_cost': hw_cost,
                'edgebanding_cost': 0,
                'total_cost': hw_cost
            })
        else:
            components = calculate_cabinet_components(
                cab['type'], cab['width'], cab['height'], cab['depth'],
                cab['has_shelves'], cab['num_shelves'],
                cab['has_drawers'], cab['num_drawers'],
                cab.get('has_false_drawers', False), cab.get('num_false_drawers', 0),
                cab.get('has_dividers', False), cab.get('num_dividers', 0),
                cab.get('has_pullout_shelves', False), cab.get('num_pullout_shelves', 0),
                cab.get('use_axial_drawers', True),
                cab['has_doors'], cab['num_doors'],
                calc_params,
                cab.get('panel_sides', 0)
            )
            hardware = calculate_hardware(
                cab['type'], cab['has_doors'], cab['num_doors'],
                cab['has_drawers'], cab['num_drawers'],
                cab['has_shelves'], cab['num_shelves'],
                cab.get('has_false_drawers', False), cab.get('num_false_drawers', 0),
                cab.get('has_dividers', False), cab.get('num_dividers', 0),
                cab.get('has_pullout_shelves', False), cab.get('num_pullout_shelves', 0),
                cab.get('use_axial_drawers', True)
            )
            mat, hw, eb = calculate_costs(
                components, hardware, cab.get('quantity', 1),
                cab['type'], cab.get('edgebanding_type', '1.0mm PVC'),
                calc_params
            )
            cabinet_data.append({
                **cab,
                'material_cost': mat,
                'hardware_cost': hw,
                'edgebanding_cost': eb,
                'total_cost': mat * 1.1 + hw + eb
            })

    totals = calculate_cabinet_totals(cabinets, calc_params)

    return render_template('index.html',
        cabinets=cabinet_data,
        totals=totals,
        current_project=current_project,
        db_connected=db_manager.is_connected()
    )

@app.route('/api/cabinets', methods=['GET'])
def get_cabinets():
    state = get_state()
    return jsonify(state.get('cabinets', []))

@app.route('/api/cabinet', methods=['POST'])
def add_cabinet():
    state = get_state()
    data = request.json

    cabinet = {
        'id': str(uuid.uuid4()),
        'code': data.get('code', ''),
        'type': data.get('type'),
        'width': float(data.get('width', 0)),
        'height': float(data.get('height', 0)),
        'depth': float(data.get('depth', 0)),
        'has_doors': data.get('has_doors', False),
        'num_doors': int(data.get('num_doors', 0)),
        'has_drawers': data.get('has_drawers', False),
        'num_drawers': int(data.get('num_drawers', 0)),
        'has_shelves': data.get('has_shelves', False),
        'num_shelves': int(data.get('num_shelves', 0)),
        'has_false_drawers': data.get('has_false_drawers', False),
        'num_false_drawers': int(data.get('num_false_drawers', 0)),
        'has_dividers': data.get('has_dividers', False),
        'num_dividers': int(data.get('num_dividers', 0)),
        'has_pullout_shelves': data.get('has_pullout_shelves', False),
        'num_pullout_shelves': int(data.get('num_pullout_shelves', 0)),
        'use_axial_drawers': data.get('use_axial_drawers', True),
        'edgebanding_type': data.get('edgebanding_type', '1.0mm PVC'),
        'quantity': int(data.get('quantity', 1)),
        # Hardware item fields
        'hardware_name': data.get('hardware_name', ''),
        'hardware_cost': float(data.get('hardware_cost', 0))
    }

    cabinets = state.get('cabinets', [])
    cabinets.append(cabinet)
    session['cabinets'] = cabinets
    session.modified = True

    return jsonify({'success': True, 'cabinet': cabinet})

@app.route('/api/cabinet/<cabinet_id>', methods=['PUT'])
def update_cabinet(cabinet_id):
    state = get_state()
    data = request.json
    cabinets = state.get('cabinets', [])

    for i, cab in enumerate(cabinets):
        if cab['id'] == cabinet_id:
            cabinets[i] = {**cab, **data}
            session['cabinets'] = cabinets
            session.modified = True
            return jsonify({'success': True})

    return jsonify({'success': False, 'error': 'Cabinet not found'}), 404

@app.route('/api/cabinet/<cabinet_id>', methods=['DELETE'])
def delete_cabinet(cabinet_id):
    state = get_state()
    cabinets = state.get('cabinets', [])
    cabinets = [c for c in cabinets if c['id'] != cabinet_id]
    session['cabinets'] = cabinets
    session.modified = True
    return jsonify({'success': True})

@app.route('/api/cabinets/quick-add', methods=['POST'])
def quick_add_cabinets():
    state = get_state()
    data = request.json
    codes = data.get('codes', '').strip().split('\n')

    added = []
    errors = []

    for code in codes:
        code = code.strip()
        if not code:
            continue

        specs = parse_cabinet_code(code)
        if specs:
            cabinet = {
                'id': str(uuid.uuid4()),
                'code': code.split(',')[0].strip().upper(),
                **specs
            }
            cabinets = session.get('cabinets', [])
            cabinets.append(cabinet)
            session['cabinets'] = cabinets
            added.append(code)
        else:
            errors.append(code)

    session.modified = True
    return jsonify({'success': True, 'added': added, 'errors': errors})

@app.route('/api/projects', methods=['GET'])
def get_projects():
    projects = db_manager.get_all_projects_with_type()
    return jsonify(projects)

@app.route('/api/project', methods=['POST'])
def create_project():
    state = get_state()
    data = request.json
    name = data.get('name', 'Untitled Project')

    # New projects start with empty cabinets but keep calc_params defaults
    cabinets = []
    calc_params = state.get('calc_params', DEFAULT_CALC_PARAMS)

    project_id = db_manager.save_project(name, cabinets, calc_params)
    if project_id:
        # Clear session cabinets for new project
        session['cabinets'] = []
        session['current_project'] = {'id': project_id, 'name': name}
        session.modified = True
        return jsonify({'success': True, 'id': project_id})
    return jsonify({'success': False, 'error': 'Failed to save project'}), 500

@app.route('/api/project/<int:project_id>', methods=['GET'])
def load_project(project_id):
    project = db_manager.load_project(project_id)
    if project:
        session['cabinets'] = project['cabinets']
        session['calc_params'] = project['calc_params']
        session['current_project'] = {'id': project['id'], 'name': project['name']}
        session.modified = True
        return jsonify({'success': True, 'project': project})
    return jsonify({'success': False, 'error': 'Project not found'}), 404

@app.route('/api/project/<int:project_id>', methods=['PUT'])
def save_project(project_id):
    state = get_state()
    current = state.get('current_project')

    if current and current['id'] == project_id:
        # Check if calc_params provided in request body (from config page)
        req_data = request.json or {}
        if 'calc_params' in req_data:
            calc_params = req_data['calc_params']
            # Also update session
            session['calc_params'] = calc_params
            session.modified = True
        else:
            calc_params = state.get('calc_params', DEFAULT_CALC_PARAMS)

        # Handle apartment complex projects differently
        if current.get('project_type') == 'apartment_complex':
            project = db_manager.load_project_with_type(project_id)
            if project:
                success = db_manager.update_apartment_complex(
                    project_id,
                    current['name'],
                    project.get('data', {'units': []}),
                    calc_params
                )
                return jsonify({'success': success})
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        else:
            # Standard project
            success = db_manager.update_project(
                project_id,
                current['name'],
                state.get('cabinets', []),
                calc_params
            )
            return jsonify({'success': success})
    return jsonify({'success': False, 'error': 'Project not loaded'}), 400

@app.route('/api/project/<int:project_id>', methods=['DELETE'])
def delete_project_route(project_id):
    success = db_manager.delete_project(project_id)
    if success:
        state = get_state()
        current_project = state.get('current_project') or {}
        if current_project.get('id') == project_id:
            session['current_project'] = None
            session.modified = True
    return jsonify({'success': success})

# ============================================================================
# KITCHEN TEMPLATE API ROUTES
# ============================================================================

@app.route('/api/templates', methods=['GET'])
def get_kitchen_templates():
    """Get kitchen templates - global plus project-specific if project loaded."""
    state = get_state()
    current_project = state.get('current_project') or {}
    project_id = request.args.get('project_id') or current_project.get('id')
    templates = db_manager.get_all_kitchen_templates(project_id)
    return jsonify(templates)

@app.route('/api/kitchen-template', methods=['POST'])
def create_kitchen_template():
    """Create a new kitchen template from current cabinets or provided data."""
    state = get_state()
    data = request.json

    name = data.get('name', 'Untitled Template')
    description = data.get('description', '')
    is_global = data.get('is_global', False)

    # If cabinets provided in request, use those; otherwise use session cabinets
    cabinets = data.get('cabinets', state.get('cabinets', []))

    if not cabinets:
        return jsonify({'success': False, 'error': 'No cabinets to save as template'}), 400

    # Get project_id if not global
    current_project = state.get('current_project') or {}
    project_id = None if is_global else current_project.get('id')

    template_id = db_manager.save_kitchen_template(name, description, cabinets, project_id, is_global)
    if template_id:
        return jsonify({'success': True, 'id': template_id})
    return jsonify({'success': False, 'error': 'Failed to save template'}), 500

@app.route('/api/kitchen-template/<int:template_id>', methods=['GET'])
def get_kitchen_template(template_id):
    """Get a single kitchen template."""
    template = db_manager.get_kitchen_template(template_id)
    if template:
        return jsonify(template)
    return jsonify({'error': 'Template not found'}), 404

@app.route('/api/kitchen-template/<int:template_id>', methods=['PUT'])
def update_kitchen_template(template_id):
    """Update a kitchen template."""
    data = request.json
    name = data.get('name')
    description = data.get('description', '')
    cabinets = data.get('cabinets')
    is_global = data.get('is_global')

    if not name or not cabinets:
        return jsonify({'success': False, 'error': 'Name and cabinets required'}), 400

    success = db_manager.update_kitchen_template(template_id, name, description, cabinets, is_global)
    return jsonify({'success': success})

@app.route('/api/kitchen-template/<int:template_id>/global', methods=['PUT'])
def set_template_global(template_id):
    """Set whether a template is global."""
    data = request.json
    is_global = data.get('is_global', False)
    success = db_manager.set_template_global(template_id, is_global)
    return jsonify({'success': success})

@app.route('/api/kitchen-template/<int:template_id>', methods=['DELETE'])
def delete_kitchen_template(template_id):
    """Delete a kitchen template."""
    success = db_manager.delete_kitchen_template(template_id)
    return jsonify({'success': success})

@app.route('/api/templates/migrate-global', methods=['POST'])
def migrate_templates_to_global():
    """Mark all existing templates as global (migration helper)."""
    success = db_manager.mark_all_templates_global()
    return jsonify({'success': success})

@app.route('/templates')
def kitchen_templates_page():
    """Template management page."""
    state = get_state()
    current_project = state.get('current_project') or {}
    project_id = current_project.get('id')
    templates = db_manager.get_all_kitchen_templates(project_id)
    standard_cabinets = db_manager.get_all_standard_cabinets(project_id)
    return render_template('kitchen_templates.html',
        templates=templates,
        standard_cabinets=standard_cabinets,
        current_project=state.get('current_project'),
        db_connected=db_manager.is_connected()
    )

# ============================================================================
# STANDARD CABINET API ROUTES
# ============================================================================

@app.route('/api/standard-cabinets', methods=['GET'])
def get_standard_cabinets():
    """Get standard cabinets - global plus project-specific if project loaded."""
    state = get_state()
    current_project = state.get('current_project') or {}
    project_id = request.args.get('project_id') or current_project.get('id')
    cabinets = db_manager.get_all_standard_cabinets(project_id)
    return jsonify(cabinets)

@app.route('/api/standard-cabinet', methods=['POST'])
def create_standard_cabinet():
    """Create a new standard cabinet preset."""
    state = get_state()
    data = request.json
    if not data.get('name'):
        return jsonify({'error': 'Name is required'}), 400

    is_global = data.get('is_global', False)
    current_project = state.get('current_project') or {}
    project_id = None if is_global else current_project.get('id')

    cabinet_id = db_manager.save_standard_cabinet(data, project_id, is_global)
    if cabinet_id:
        return jsonify({'success': True, 'id': cabinet_id})
    return jsonify({'error': 'Failed to create standard cabinet'}), 500

@app.route('/api/standard-cabinet/<int:cabinet_id>', methods=['GET'])
def get_standard_cabinet(cabinet_id):
    """Get a single standard cabinet preset."""
    cabinet = db_manager.get_standard_cabinet(cabinet_id)
    if cabinet:
        return jsonify(cabinet)
    return jsonify({'error': 'Standard cabinet not found'}), 404

@app.route('/api/standard-cabinet/<int:cabinet_id>', methods=['PUT'])
def update_standard_cabinet(cabinet_id):
    """Update a standard cabinet preset."""
    data = request.json
    if not data.get('name'):
        return jsonify({'error': 'Name is required'}), 400

    is_global = data.get('is_global')
    success = db_manager.update_standard_cabinet(cabinet_id, data, is_global)
    return jsonify({'success': success})

@app.route('/api/standard-cabinet/<int:cabinet_id>/global', methods=['PUT'])
def set_standard_cabinet_global(cabinet_id):
    """Set whether a standard cabinet is global."""
    data = request.json
    is_global = data.get('is_global', False)
    success = db_manager.set_standard_cabinet_global(cabinet_id, is_global)
    return jsonify({'success': success})

@app.route('/api/standard-cabinet/<int:cabinet_id>', methods=['DELETE'])
def delete_standard_cabinet(cabinet_id):
    """Delete a standard cabinet preset."""
    success = db_manager.delete_standard_cabinet(cabinet_id)
    return jsonify({'success': success})

@app.route('/api/standard-cabinets/migrate-global', methods=['POST'])
def migrate_standard_cabinets_to_global():
    """Mark all existing standard cabinets as global (migration helper)."""
    success = db_manager.mark_all_standard_cabinets_global()
    return jsonify({'success': success})

@app.route('/api/export/csv/standard-cabinet/<int:cabinet_id>')
def export_standard_cabinet_csv(cabinet_id):
    """Export BOM and materials for a single standard cabinet to CSV."""
    cab = db_manager.get_standard_cabinet(cabinet_id)
    if not cab:
        return jsonify({'error': 'Cabinet not found'}), 404

    state = get_state()
    calc_params = state.get('calc_params', DEFAULT_CALC_PARAMS)

    # Calculate components and hardware
    components = calculate_cabinet_components(
        cab['type'], cab['width'], cab['height'], cab['depth'],
        cab.get('has_shelves', False), cab.get('num_shelves', 0),
        cab.get('has_drawers', False), cab.get('num_drawers', 0),
        cab.get('has_false_drawers', False), cab.get('num_false_drawers', 0),
        cab.get('has_dividers', False), cab.get('num_dividers', 0),
        cab.get('has_pullout_shelves', False), cab.get('num_pullout_shelves', 0),
        cab.get('use_axial_drawers', True),
        cab.get('has_doors', False), cab.get('num_doors', 0),
        calc_params,
        cab.get('panel_sides', 0)
    )
    hardware = calculate_hardware(
        cab['type'], cab.get('has_doors', False), cab.get('num_doors', 0),
        cab.get('has_drawers', False), cab.get('num_drawers', 0),
        cab.get('has_shelves', False), cab.get('num_shelves', 0),
        cab.get('has_false_drawers', False), cab.get('num_false_drawers', 0),
        cab.get('has_dividers', False), cab.get('num_dividers', 0),
        cab.get('has_pullout_shelves', False), cab.get('num_pullout_shelves', 0),
        cab.get('use_axial_drawers', True)
    )

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Cabinet info header
    writer.writerow(['STANDARD CABINET BOM EXPORT'])
    writer.writerow(['Name:', cab['name']])
    writer.writerow(['Code:', cab.get('code', '')])
    writer.writerow(['Type:', cab['type']])
    writer.writerow(['Dimensions:', f"{cab['width']}W x {cab['height']}H x {cab['depth']}D"])
    writer.writerow([])

    # Materials/Components section
    writer.writerow(['BILL OF MATERIALS'])
    writer.writerow(['Component', 'Length (in)', 'Height (in)', 'Qty', 'Sq Ft', 'Material Type'])

    # Group by material type for totals
    material_totals = {
        'Primary Substrate': 0,
        'Back Panel': 0,
        'Drawer Material': 0,
        'Door/Drawer Fronts': 0
    }

    for comp_name, comp_data in components.items():
        sqft = calculate_sq_feet(comp_data["length"], comp_data["height"]) * comp_data["qty"]
        material = comp_data["material"]
        material_totals[material] = material_totals.get(material, 0) + sqft
        writer.writerow([
            comp_name,
            f"{comp_data['length']:.3f}",
            f"{comp_data['height']:.3f}",
            comp_data['qty'],
            f"{sqft:.2f}",
            material
        ])

    writer.writerow([])
    writer.writerow(['MATERIAL TOTALS'])
    writer.writerow(['Material Type', 'Total Sq Ft'])
    for material, total in material_totals.items():
        if total > 0:
            writer.writerow([material, f"{total:.2f}"])

    writer.writerow([])

    # Hardware section
    writer.writerow(['HARDWARE'])
    writer.writerow(['Item', 'Quantity'])
    for item, qty in hardware.items():
        if qty > 0:
            writer.writerow([item, qty])

    # Generate filename
    safe_name = "".join(c for c in cab['name'] if c.isalnum() or c in (' ', '-', '_')).strip()
    filename = f"{safe_name}-BOM-{datetime.now().strftime('%Y%m%d')}.csv"

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )

@app.route('/standard-cabinets')
def standard_cabinets_page():
    """Standard cabinet presets management page."""
    state = get_state()
    current_project = state.get('current_project') or {}
    project_id = current_project.get('id')
    cabinets = db_manager.get_all_standard_cabinets(project_id)
    return render_template('standard_cabinets.html',
        cabinets=cabinets,
        current_project=state.get('current_project'),
        db_connected=db_manager.is_connected()
    )

# ============================================================================
# APARTMENT COMPLEX API ROUTES
# ============================================================================

def resolve_unit_items(unit: Dict, calc_params: Dict) -> List[Dict]:
    """
    Resolve a unit's items array into a flat list of cabinet specifications.
    - Template references: fetch template, expand its cabinets
    - Standard cabinet references: fetch cabinet spec
    - Custom/inline: use as-is

    Also handles legacy format (kitchen_cabinets + bathroom_cabinets).
    """
    # Check for new format with items array
    if 'items' in unit:
        resolved_cabinets = []
        for item in unit.get('items', []):
            item_type = item.get('type')
            quantity = item.get('quantity', 1)

            if item_type == 'template':
                # Load template and expand its cabinets
                template = db_manager.get_kitchen_template(item.get('template_id'))
                if template:
                    template_cabinets = template.get('cabinets', [])
                    for template_cab in template_cabinets:
                        # Template cabinet could also be a standard ref or inline
                        if template_cab.get('type') == 'standard':
                            std_cab = db_manager.get_standard_cabinet(template_cab.get('standard_cabinet_id'))
                            if std_cab:
                                cab_qty = template_cab.get('quantity', 1) * quantity
                                resolved_cabinets.append({**std_cab, 'quantity': cab_qty})
                        else:
                            # Inline cabinet in template
                            cab_qty = template_cab.get('quantity', 1) * quantity
                            resolved_cabinets.append({**template_cab, 'quantity': cab_qty})

            elif item_type == 'standard':
                # Load standard cabinet
                std_cab = db_manager.get_standard_cabinet(item.get('standard_cabinet_id'))
                if std_cab:
                    resolved_cabinets.append({**std_cab, 'quantity': quantity})

            elif item_type == 'custom':
                # Custom/inline cabinet - map cabinet_type to type for calculation
                custom_cab = {**item}
                # The item stores type='custom' as item type, but cabinet_type holds the actual type
                if 'cabinet_type' in custom_cab:
                    custom_cab['type'] = custom_cab['cabinet_type']
                resolved_cabinets.append(custom_cab)

        return resolved_cabinets
    else:
        # Legacy format - combine kitchen and bathroom cabinets
        kitchen_cabinets = unit.get('kitchen_cabinets', [])
        bathroom_cabinets = unit.get('bathroom_cabinets', [])
        return kitchen_cabinets + bathroom_cabinets

def calculate_unit_costs(unit: Dict, calc_params: Dict) -> Dict:
    """Calculate costs for a single unit by resolving all item references."""
    resolved_cabinets = resolve_unit_items(unit, calc_params)
    costs = calculate_cabinet_totals(resolved_cabinets, calc_params)

    return {
        'cabinets': costs,
        'cabinet_count': len(resolved_cabinets),
        'unit_total': {
            'material': costs['material'],
            'hardware': costs['hardware'],
            'edgebanding': costs['edgebanding'],
            'total': costs['total'],  # Base cost
            'marked_up_total': costs['marked_up']['total']  # With markups applied
        }
    }

def calculate_complex_costs(data: Dict, calc_params: Dict) -> Dict:
    """Calculate aggregate costs for entire apartment complex."""
    units = data.get('units', [])

    total_material = 0
    total_hardware = 0
    total_edgebanding = 0
    total_cost = 0
    total_marked_up = 0
    unit_breakdown = []

    for unit in units:
        unit_costs = calculate_unit_costs(unit, calc_params)
        unit_breakdown.append({
            'unit_number': unit['unit_number'],
            'costs': unit_costs
        })
        total_material += unit_costs['unit_total']['material']
        total_hardware += unit_costs['unit_total']['hardware']
        total_edgebanding += unit_costs['unit_total']['edgebanding']
        total_cost += unit_costs['unit_total']['total']
        total_marked_up += unit_costs['unit_total']['marked_up_total']

    return {
        'unit_breakdown': unit_breakdown,
        'complex_totals': {
            'material': total_material,
            'hardware': total_hardware,
            'edgebanding': total_edgebanding,
            'total': total_cost,
            'marked_up_total': total_marked_up,
            'unit_count': len(units)
        }
    }

@app.route('/api/project/apartment-complex', methods=['POST'])
def create_apartment_complex():
    """Create a new apartment complex project."""
    state = get_state()
    data = request.json

    name = data.get('name', 'Untitled Complex')
    complex_data = {
        'units': []
    }
    calc_params = state.get('calc_params', DEFAULT_CALC_PARAMS)

    project_id = db_manager.save_apartment_complex(name, complex_data, calc_params)
    if project_id:
        session['current_project'] = {
            'id': project_id,
            'name': name,
            'project_type': 'apartment_complex'
        }
        session.modified = True
        return jsonify({'success': True, 'id': project_id})
    return jsonify({'success': False, 'error': 'Failed to create complex'}), 500

@app.route('/api/project/<int:project_id>/units', methods=['GET'])
def get_units(project_id):
    """Get all units for an apartment complex."""
    project = db_manager.load_project_with_type(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    if project.get('project_type') != 'apartment_complex':
        return jsonify({'error': 'Not a multi unit project'}), 400

    data = project.get('data', {'units': []})
    calc_params = project.get('calc_params', DEFAULT_CALC_PARAMS)

    # Calculate costs for each unit
    units_with_costs = []
    for unit in data.get('units', []):
        costs = calculate_unit_costs(unit, calc_params)
        units_with_costs.append({
            **unit,
            'costs': costs
        })

    return jsonify({
        'units': units_with_costs,
        'totals': calculate_complex_costs(data, calc_params)['complex_totals']
    })

@app.route('/api/project/<int:project_id>/unit', methods=['POST'])
def add_unit(project_id):
    """Add a new unit to an apartment complex."""
    project = db_manager.load_project_with_type(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    if project.get('project_type') != 'apartment_complex':
        return jsonify({'error': 'Not a multi unit project'}), 400

    req_data = request.json
    unit_number = req_data.get('unit_number', '')

    # New format: items array
    items = req_data.get('items', [])

    # Legacy support: if template_id provided, add as template item
    if not items and req_data.get('kitchen_template_id'):
        items.append({
            'id': str(uuid.uuid4()),
            'type': 'template',
            'template_id': int(req_data['kitchen_template_id']),
            'quantity': 1
        })

    new_unit = {
        'id': str(uuid.uuid4()),
        'unit_number': unit_number,
        'items': items
    }

    data = project.get('data', {'units': []})
    data['units'].append(new_unit)

    success = db_manager.update_apartment_complex(
        project_id,
        project['name'],
        data,
        project.get('calc_params', DEFAULT_CALC_PARAMS)
    )

    if success:
        return jsonify({'success': True, 'unit': new_unit})
    return jsonify({'success': False, 'error': 'Failed to add unit'}), 500

@app.route('/api/project/<int:project_id>/unit/<unit_id>', methods=['GET'])
def get_unit(project_id, unit_id):
    """Get a single unit's details."""
    project = db_manager.load_project_with_type(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = project.get('data', {'units': []})
    for unit in data.get('units', []):
        if unit['id'] == unit_id:
            costs = calculate_unit_costs(unit, project.get('calc_params', DEFAULT_CALC_PARAMS))
            return jsonify({**unit, 'costs': costs})

    return jsonify({'error': 'Unit not found'}), 404

@app.route('/api/project/<int:project_id>/unit/<unit_id>', methods=['PUT'])
def update_unit(project_id, unit_id):
    """Update a unit's configuration."""
    project = db_manager.load_project_with_type(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    req_data = request.json
    data = project.get('data', {'units': []})

    for i, unit in enumerate(data.get('units', [])):
        if unit['id'] == unit_id:
            # Update fields
            if 'unit_number' in req_data:
                unit['unit_number'] = req_data['unit_number']

            # New format: items array
            if 'items' in req_data:
                unit['items'] = req_data['items']

            # Legacy support
            if 'kitchen_template_id' in req_data:
                unit['kitchen_template_id'] = req_data['kitchen_template_id']
                if req_data['kitchen_template_id']:
                    template = db_manager.get_kitchen_template(int(req_data['kitchen_template_id']))
                    if template:
                        unit['kitchen_cabinets'] = template.get('cabinets', [])
            if 'kitchen_cabinets' in req_data:
                unit['kitchen_cabinets'] = req_data['kitchen_cabinets']
            if 'bathroom_cabinets' in req_data:
                unit['bathroom_cabinets'] = req_data['bathroom_cabinets']

            data['units'][i] = unit

            success = db_manager.update_apartment_complex(
                project_id,
                project['name'],
                data,
                project.get('calc_params', DEFAULT_CALC_PARAMS)
            )

            if success:
                return jsonify({'success': True, 'unit': unit})
            return jsonify({'success': False, 'error': 'Failed to update unit'}), 500

    return jsonify({'error': 'Unit not found'}), 404

@app.route('/api/project/<int:project_id>/unit/<unit_id>', methods=['DELETE'])
def delete_unit(project_id, unit_id):
    """Delete a unit from an apartment complex."""
    project = db_manager.load_project_with_type(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = project.get('data', {'units': []})
    original_count = len(data.get('units', []))
    data['units'] = [u for u in data.get('units', []) if u['id'] != unit_id]

    if len(data['units']) == original_count:
        return jsonify({'error': 'Unit not found'}), 404

    success = db_manager.update_apartment_complex(
        project_id,
        project['name'],
        data,
        project.get('calc_params', DEFAULT_CALC_PARAMS)
    )

    return jsonify({'success': success})

# ============================================================================
# UNIT ITEM MANAGEMENT ROUTES
# ============================================================================

@app.route('/api/project/<int:project_id>/unit/<unit_id>/item', methods=['POST'])
def add_unit_item(project_id, unit_id):
    """Add an item (template, standard cabinet, or custom) to a unit."""
    project = db_manager.load_project_with_type(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    req_data = request.json
    item_type = req_data.get('type')

    if item_type not in ['template', 'standard', 'custom']:
        return jsonify({'error': 'Invalid item type'}), 400

    data = project.get('data', {'units': []})

    for i, unit in enumerate(data.get('units', [])):
        if unit['id'] == unit_id:
            # Initialize items array if not present (migration from old format)
            if 'items' not in unit:
                unit['items'] = []

            # Build the new item
            new_item = {
                'id': str(uuid.uuid4()),
                'type': item_type
            }

            if item_type == 'template':
                new_item['template_id'] = req_data.get('template_id')
                new_item['quantity'] = req_data.get('quantity', 1)
            elif item_type == 'standard':
                new_item['standard_cabinet_id'] = req_data.get('standard_cabinet_id')
                new_item['quantity'] = req_data.get('quantity', 1)
            elif item_type == 'custom':
                # Copy all cabinet fields
                new_item['code'] = req_data.get('code', '')
                new_item['cabinet_type'] = req_data.get('cabinet_type', 'Base Cabinets')
                new_item['width'] = req_data.get('width', 24)
                new_item['height'] = req_data.get('height', 34.5)
                new_item['depth'] = req_data.get('depth', 24)
                new_item['has_doors'] = req_data.get('has_doors', False)
                new_item['num_doors'] = req_data.get('num_doors', 0)
                new_item['has_drawers'] = req_data.get('has_drawers', False)
                new_item['num_drawers'] = req_data.get('num_drawers', 0)
                new_item['has_shelves'] = req_data.get('has_shelves', False)
                new_item['num_shelves'] = req_data.get('num_shelves', 0)
                new_item['quantity'] = req_data.get('quantity', 1)
                new_item['edgebanding_type'] = req_data.get('edgebanding_type', '1.0mm PVC')

            unit['items'].append(new_item)
            data['units'][i] = unit

            success = db_manager.update_apartment_complex(
                project_id,
                project['name'],
                data,
                project.get('calc_params', DEFAULT_CALC_PARAMS)
            )

            if success:
                return jsonify({'success': True, 'item': new_item})
            return jsonify({'success': False, 'error': 'Failed to add item'}), 500

    return jsonify({'error': 'Unit not found'}), 404

@app.route('/api/project/<int:project_id>/unit/<unit_id>/item/<item_id>', methods=['PUT'])
def update_unit_item(project_id, unit_id, item_id):
    """Update an item in a unit."""
    project = db_manager.load_project_with_type(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    req_data = request.json
    data = project.get('data', {'units': []})

    for i, unit in enumerate(data.get('units', [])):
        if unit['id'] == unit_id:
            items = unit.get('items', [])
            for j, item in enumerate(items):
                if item['id'] == item_id:
                    # Update quantity for any type
                    if 'quantity' in req_data:
                        item['quantity'] = req_data['quantity']

                    # Type-specific updates
                    if item['type'] == 'template' and 'template_id' in req_data:
                        item['template_id'] = req_data['template_id']
                    elif item['type'] == 'standard' and 'standard_cabinet_id' in req_data:
                        item['standard_cabinet_id'] = req_data['standard_cabinet_id']
                    elif item['type'] == 'custom':
                        # Update any custom cabinet fields
                        for field in ['code', 'cabinet_type', 'width', 'height', 'depth',
                                      'has_doors', 'num_doors', 'has_drawers', 'num_drawers',
                                      'has_shelves', 'num_shelves', 'edgebanding_type']:
                            if field in req_data:
                                item[field] = req_data[field]

                    items[j] = item
                    unit['items'] = items
                    data['units'][i] = unit

                    success = db_manager.update_apartment_complex(
                        project_id,
                        project['name'],
                        data,
                        project.get('calc_params', DEFAULT_CALC_PARAMS)
                    )

                    if success:
                        return jsonify({'success': True, 'item': item})
                    return jsonify({'success': False, 'error': 'Failed to update item'}), 500

            return jsonify({'error': 'Item not found'}), 404

    return jsonify({'error': 'Unit not found'}), 404

@app.route('/api/project/<int:project_id>/unit/<unit_id>/item/<item_id>', methods=['DELETE'])
def delete_unit_item(project_id, unit_id, item_id):
    """Delete an item from a unit."""
    project = db_manager.load_project_with_type(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = project.get('data', {'units': []})

    for i, unit in enumerate(data.get('units', [])):
        if unit['id'] == unit_id:
            items = unit.get('items', [])
            original_count = len(items)
            unit['items'] = [item for item in items if item['id'] != item_id]

            if len(unit['items']) == original_count:
                return jsonify({'error': 'Item not found'}), 404

            data['units'][i] = unit

            success = db_manager.update_apartment_complex(
                project_id,
                project['name'],
                data,
                project.get('calc_params', DEFAULT_CALC_PARAMS)
            )

            return jsonify({'success': success})

    return jsonify({'error': 'Unit not found'}), 404

# ============================================================================
# LEGACY BATHROOM CABINET ROUTES (for backward compatibility)
# ============================================================================

@app.route('/api/project/<int:project_id>/unit/<unit_id>/bathroom/cabinet', methods=['POST'])
def add_bathroom_cabinet(project_id, unit_id):
    """Add a cabinet to a unit's bathroom."""
    project = db_manager.load_project_with_type(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    req_data = request.json
    data = project.get('data', {'units': []})

    for i, unit in enumerate(data.get('units', [])):
        if unit['id'] == unit_id:
            cabinet = {
                'id': str(uuid.uuid4()),
                'code': req_data.get('code', ''),
                'type': req_data.get('type'),
                'width': float(req_data.get('width')),
                'height': float(req_data.get('height')),
                'depth': float(req_data.get('depth')),
                'has_doors': req_data.get('has_doors', False),
                'num_doors': int(req_data.get('num_doors', 0)),
                'has_drawers': req_data.get('has_drawers', False),
                'num_drawers': int(req_data.get('num_drawers', 0)),
                'has_shelves': req_data.get('has_shelves', False),
                'num_shelves': int(req_data.get('num_shelves', 0)),
                'has_false_drawers': req_data.get('has_false_drawers', False),
                'num_false_drawers': int(req_data.get('num_false_drawers', 0)),
                'has_dividers': req_data.get('has_dividers', False),
                'num_dividers': int(req_data.get('num_dividers', 0)),
                'has_pullout_shelves': req_data.get('has_pullout_shelves', False),
                'num_pullout_shelves': int(req_data.get('num_pullout_shelves', 0)),
                'use_axial_drawers': req_data.get('use_axial_drawers', True),
                'edgebanding_type': req_data.get('edgebanding_type', '1.0mm PVC'),
                'quantity': int(req_data.get('quantity', 1))
            }

            if 'bathroom_cabinets' not in unit:
                unit['bathroom_cabinets'] = []
            unit['bathroom_cabinets'].append(cabinet)
            data['units'][i] = unit

            success = db_manager.update_apartment_complex(
                project_id,
                project['name'],
                data,
                project.get('calc_params', DEFAULT_CALC_PARAMS)
            )

            if success:
                return jsonify({'success': True, 'cabinet': cabinet})
            return jsonify({'success': False, 'error': 'Failed to add cabinet'}), 500

    return jsonify({'error': 'Unit not found'}), 404

@app.route('/api/project/<int:project_id>/unit/<unit_id>/bathroom/cabinet/<cabinet_id>', methods=['DELETE'])
def delete_bathroom_cabinet(project_id, unit_id, cabinet_id):
    """Delete a cabinet from a unit's bathroom."""
    project = db_manager.load_project_with_type(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = project.get('data', {'units': []})

    for i, unit in enumerate(data.get('units', [])):
        if unit['id'] == unit_id:
            original_count = len(unit.get('bathroom_cabinets', []))
            unit['bathroom_cabinets'] = [c for c in unit.get('bathroom_cabinets', []) if c['id'] != cabinet_id]

            if len(unit['bathroom_cabinets']) == original_count:
                return jsonify({'error': 'Cabinet not found'}), 404

            data['units'][i] = unit

            success = db_manager.update_apartment_complex(
                project_id,
                project['name'],
                data,
                project.get('calc_params', DEFAULT_CALC_PARAMS)
            )

            return jsonify({'success': success})

    return jsonify({'error': 'Unit not found'}), 404

@app.route('/api/project/<int:project_id>/costs', methods=['GET'])
def get_complex_costs(project_id):
    """Get cost breakdown for an apartment complex."""
    project = db_manager.load_project_with_type(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = project.get('data', {'units': []})
    calc_params = project.get('calc_params', DEFAULT_CALC_PARAMS)

    return jsonify(calculate_complex_costs(data, calc_params))

@app.route('/complex/<int:project_id>')
def complex_page(project_id):
    """Apartment complex overview page."""
    project = db_manager.load_project_with_type(project_id)
    if not project:
        return "Project not found", 404
    if project.get('project_type') != 'apartment_complex':
        return redirect('/')

    state = get_state()
    session['current_project'] = {
        'id': project['id'],
        'name': project['name'],
        'project_type': 'apartment_complex'
    }

    # Load project's calc_params into session
    data = project.get('data', {'units': []})
    calc_params = project.get('calc_params', DEFAULT_CALC_PARAMS)
    session['calc_params'] = calc_params
    session.modified = True
    templates = db_manager.get_all_kitchen_templates(project_id)

    # Calculate costs for each unit
    units_with_costs = []
    for unit in data.get('units', []):
        costs = calculate_unit_costs(unit, calc_params)
        # Get template name
        template_name = None
        if unit.get('kitchen_template_id'):
            for t in templates:
                if str(t['id']) == str(unit['kitchen_template_id']):
                    template_name = t['name']
                    break

        # Enrich items with cabinet counts for templates
        enriched_items = []
        for item in unit.get('items', []):
            enriched_item = {**item}
            if item.get('type') == 'template':
                # Find the template and count its cabinets
                template_id = item.get('template_id')
                for t in templates:
                    if str(t['id']) == str(template_id):
                        template_cabs = t.get('cabinets', [])
                        # Sum quantities of cabinets within the template
                        cab_count = sum(c.get('quantity', 1) for c in template_cabs)
                        enriched_item['cabinet_count'] = cab_count * item.get('quantity', 1)
                        break
                else:
                    enriched_item['cabinet_count'] = item.get('quantity', 1)
            else:
                enriched_item['cabinet_count'] = item.get('quantity', 1)
            enriched_items.append(enriched_item)

        units_with_costs.append({
            **unit,
            'items': enriched_items,
            'costs': costs,
            'template_name': template_name
        })

    totals = calculate_complex_costs(data, calc_params)['complex_totals']

    return render_template('complex.html',
        project=project,
        units=units_with_costs,
        totals=totals,
        templates=templates,
        current_project=session.get('current_project'),
        db_connected=db_manager.is_connected()
    )

@app.route('/unit/<int:project_id>/<unit_id>')
def unit_page(project_id, unit_id):
    """Unit configuration page."""
    project = db_manager.load_project_with_type(project_id)
    if not project:
        return "Project not found", 404

    data = project.get('data', {'units': []})
    calc_params = project.get('calc_params', DEFAULT_CALC_PARAMS)

    # Load project's calc_params into session
    session['current_project'] = {
        'id': project['id'],
        'name': project['name'],
        'project_type': project.get('project_type', 'standard')
    }
    session['calc_params'] = calc_params
    session.modified = True

    templates = db_manager.get_all_kitchen_templates(project_id)
    standard_cabinets = db_manager.get_all_standard_cabinets(project_id)

    # Find the unit and its index
    unit = None
    unit_index = -1
    for i, u in enumerate(data.get('units', [])):
        if u['id'] == unit_id:
            unit = u
            unit_index = i
            break

    if not unit:
        return "Unit not found", 404

    # Get prev/next unit IDs
    units = data.get('units', [])
    prev_unit_id = units[unit_index - 1]['id'] if unit_index > 0 else None
    next_unit_id = units[unit_index + 1]['id'] if unit_index < len(units) - 1 else None

    # Calculate costs
    costs = calculate_unit_costs(unit, calc_params)

    # Calculate bathroom cabinet costs
    bathroom_cabinets_with_costs = []
    for cab in unit.get('bathroom_cabinets', []):
        components = calculate_cabinet_components(
            cab['type'], cab['width'], cab['height'], cab['depth'],
            cab.get('has_shelves', False), cab.get('num_shelves', 0),
            cab.get('has_drawers', False), cab.get('num_drawers', 0),
            cab.get('has_false_drawers', False), cab.get('num_false_drawers', 0),
            cab.get('has_dividers', False), cab.get('num_dividers', 0),
            cab.get('has_pullout_shelves', False), cab.get('num_pullout_shelves', 0),
            cab.get('use_axial_drawers', True),
            cab.get('has_doors', False), cab.get('num_doors', 0),
            calc_params,
            cab.get('panel_sides', 0)
        )
        hardware = calculate_hardware(
            cab['type'], cab.get('has_doors', False), cab.get('num_doors', 0),
            cab.get('has_drawers', False), cab.get('num_drawers', 0),
            cab.get('has_shelves', False), cab.get('num_shelves', 0),
            cab.get('has_false_drawers', False), cab.get('num_false_drawers', 0),
            cab.get('has_dividers', False), cab.get('num_dividers', 0),
            cab.get('has_pullout_shelves', False), cab.get('num_pullout_shelves', 0),
            cab.get('use_axial_drawers', True)
        )
        mat, hw, eb = calculate_costs(
            components, hardware, cab.get('quantity', 1),
            cab['type'], cab.get('edgebanding_type', '1.0mm PVC'),
            calc_params
        )
        bathroom_cabinets_with_costs.append({
            **cab,
            'material_cost': mat,
            'hardware_cost': hw,
            'edgebanding_cost': eb,
            'total_cost': mat + hw + eb
        })

    return render_template('unit.html',
        project=project,
        unit=unit,
        unit_index=unit_index,
        prev_unit_id=prev_unit_id,
        next_unit_id=next_unit_id,
        costs=costs,
        templates=templates,
        standard_cabinets=standard_cabinets,
        bathroom_cabinets=bathroom_cabinets_with_costs,
        current_project=session.get('current_project'),
        db_connected=db_manager.is_connected()
    )

@app.route('/api/config', methods=['GET'])
def get_config():
    state = get_state()
    return jsonify(state.get('calc_params', DEFAULT_CALC_PARAMS))

@app.route('/api/config', methods=['PUT'])
def update_config():
    data = request.json
    session['calc_params'] = data
    session.modified = True
    return jsonify({'success': True})

@app.route('/config')
def config_page():
    state = get_state()
    pricing_rules = db_manager.get_all_pricing_rules()
    return render_template('config.html',
        calc_params=state.get('calc_params', DEFAULT_CALC_PARAMS),
        current_project=state.get('current_project'),
        pricing_rules=pricing_rules,
        db_connected=db_manager.is_connected()
    )

@app.route('/settings')
def settings_page():
    """Admin settings page for pricing rules."""
    state = get_state()
    pricing_rules = db_manager.get_all_pricing_rules()
    return render_template('settings.html',
        pricing_rules=pricing_rules,
        current_project=state.get('current_project'),
        db_connected=db_manager.is_connected()
    )

# ============================================================================
# PRICING RULES API ROUTES
# ============================================================================

@app.route('/api/pricing-rules', methods=['GET'])
def get_pricing_rules():
    """Get all pricing rules."""
    rules = db_manager.get_all_pricing_rules()
    return jsonify(rules)

@app.route('/api/pricing-rule', methods=['POST'])
def create_pricing_rule():
    """Create a new pricing rule."""
    data = request.json
    if not data.get('name'):
        return jsonify({'error': 'Name is required'}), 400

    rule_id = db_manager.save_pricing_rule(data)
    if rule_id:
        return jsonify({'success': True, 'id': rule_id})
    return jsonify({'error': 'Failed to create pricing rule'}), 500

@app.route('/api/pricing-rule/<int:rule_id>', methods=['GET'])
def get_pricing_rule(rule_id):
    """Get a single pricing rule."""
    rule = db_manager.get_pricing_rule(rule_id)
    if rule:
        return jsonify(rule)
    return jsonify({'error': 'Pricing rule not found'}), 404

@app.route('/api/pricing-rule/<int:rule_id>', methods=['PUT'])
def update_pricing_rule(rule_id):
    """Update a pricing rule."""
    data = request.json
    if not data.get('name'):
        return jsonify({'error': 'Name is required'}), 400

    success = db_manager.update_pricing_rule(rule_id, data)
    return jsonify({'success': success})

@app.route('/api/pricing-rule/<int:rule_id>', methods=['DELETE'])
def delete_pricing_rule(rule_id):
    """Delete a pricing rule."""
    success = db_manager.delete_pricing_rule(rule_id)
    return jsonify({'success': success})

# ============================================================================
# COMPANY SETTINGS API ROUTES
# ============================================================================

@app.route('/api/company-settings', methods=['GET'])
def get_company_settings():
    """Get company settings."""
    settings = db_manager.get_company_settings()
    if settings:
        return jsonify(settings)
    return jsonify({
        'company_name': '',
        'company_address': '',
        'company_phone': '',
        'company_email': ''
    })

@app.route('/api/company-settings', methods=['PUT'])
def save_company_settings():
    """Save company settings."""
    data = request.json
    success = db_manager.save_company_settings(data)
    return jsonify({'success': success})

@app.route('/projects')
def projects_page():
    state = get_state()
    projects = db_manager.get_all_projects_with_type()
    return render_template('projects.html',
        projects=projects,
        current_project=state.get('current_project'),
        db_connected=db_manager.is_connected()
    )

@app.route('/api/export/pdf', methods=['POST'])
def export_pdf():
    """Generate a PDF quote with customer info, line items, and totals."""
    state = get_state()
    cabinets = state.get('cabinets', [])
    calc_params = state.get('calc_params', DEFAULT_CALC_PARAMS)
    current_project = state.get('current_project')

    # Get form data (tax/shipping/notes from request)
    data = request.json or {}

    # Get company info from database
    company_settings = db_manager.get_company_settings() or {}
    company_name = company_settings.get('company_name', '')
    company_address = company_settings.get('company_address', '')
    company_phone = company_settings.get('company_phone', '')
    company_email = company_settings.get('company_email', '')
    tax_rate = float(data.get('tax_rate', 0))
    shipping = float(data.get('shipping', 0))
    notes = data.get('notes', '')

    # Get customer info from calc_params (project settings)
    customer_name = calc_params.get('customer_name', '')
    customer_address = calc_params.get('customer_address', '')
    customer_phone = calc_params.get('customer_phone', '')
    customer_email = calc_params.get('customer_email', '')

    # Calculate line items with sale price (marked up)
    items = []
    subtotal = 0

    for cab in cabinets:
        components = calculate_cabinet_components(
            cab['type'], cab['width'], cab['height'], cab['depth'],
            cab['has_shelves'], cab['num_shelves'],
            cab['has_drawers'], cab['num_drawers'],
            cab.get('has_false_drawers', False), cab.get('num_false_drawers', 0),
            cab.get('has_dividers', False), cab.get('num_dividers', 0),
            cab.get('has_pullout_shelves', False), cab.get('num_pullout_shelves', 0),
            cab.get('use_axial_drawers', True),
            cab['has_doors'], cab['num_doors'],
            calc_params,
            cab.get('panel_sides', 0)
        )
        hardware = calculate_hardware(
            cab['type'], cab['has_doors'], cab['num_doors'],
            cab['has_drawers'], cab['num_drawers'],
            cab['has_shelves'], cab['num_shelves'],
            cab.get('has_false_drawers', False), cab.get('num_false_drawers', 0),
            cab.get('has_dividers', False), cab.get('num_dividers', 0),
            cab.get('has_pullout_shelves', False), cab.get('num_pullout_shelves', 0),
            cab.get('use_axial_drawers', True)
        )

        # Get detailed costs and apply markups for sale price
        detailed = calculate_costs_detailed(
            components, hardware, 1,  # quantity=1 for unit price
            cab['type'], cab.get('edgebanding_type', '1.0mm PVC'),
            calc_params
        )
        marked_up = apply_markups(detailed, calc_params)
        unit_price = marked_up['total']

        quantity = cab.get('quantity', 1)
        line_total = unit_price * quantity
        subtotal += line_total

        # Build description
        desc_parts = [cab['type'].replace(' Cabinets', '').replace(' Cabinet', '')]
        if cab['has_doors'] and cab['num_doors'] > 0:
            desc_parts.append(f"{cab['num_doors']} door{'s' if cab['num_doors'] > 1 else ''}")
        if cab['has_drawers'] and cab['num_drawers'] > 0:
            desc_parts.append(f"{cab['num_drawers']} drawer{'s' if cab['num_drawers'] > 1 else ''}")

        items.append({
            'code': cab.get('code', ''),
            'description': ', '.join(desc_parts),
            'dimensions': f"{cab['width']}\"W x {cab['height']}\"H x {cab['depth']}\"D",
            'quantity': quantity,
            'unit_price': unit_price,
            'line_total': line_total
        })

    # Calculate totals
    tax_amount = subtotal * (tax_rate / 100)
    grand_total = subtotal + tax_amount + shipping

    # Generate quote number and date
    quote_number = f"Q-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    quote_date = datetime.now().strftime('%B %d, %Y')
    project_name = current_project['name'] if current_project else 'Cabinet Quote'

    # Create PDF in memory
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
    elements = []
    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=18, spaceAfter=6)
    header_style = ParagraphStyle('Header', parent=styles['Normal'], fontSize=10, textColor=colors.grey)
    normal_style = ParagraphStyle('CustomNormal', parent=styles['Normal'], fontSize=10)
    bold_style = ParagraphStyle('Bold', parent=styles['Normal'], fontSize=10, fontName='Helvetica-Bold')
    right_style = ParagraphStyle('Right', parent=styles['Normal'], fontSize=10, alignment=TA_RIGHT)
    notes_style = ParagraphStyle('Notes', parent=styles['Normal'], fontSize=9, textColor=colors.grey)

    # Header section - Company and Quote info side by side
    if company_name:
        company_info = f"<b>{company_name}</b><br/>"
        if company_address:
            company_info += company_address.replace('\n', '<br/>') + "<br/>"
        if company_phone:
            company_info += f"Phone: {company_phone}<br/>"
        if company_email:
            company_info += f"Email: {company_email}"
    else:
        company_info = "<b>Your Company Name</b><br/>Configure in Settings"

    quote_info = f"<b>QUOTE</b><br/>Quote #: {quote_number}<br/>Date: {quote_date}"

    header_table = Table([
        [Paragraph(company_info, normal_style), Paragraph(quote_info, right_style)]
    ], colWidths=[4*inch, 3*inch])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 0.3*inch))

    # Customer section
    if customer_name:
        customer_info = f"<b>Quote For:</b><br/>{customer_name}"
        if customer_address:
            customer_info += f"<br/>{customer_address.replace(chr(10), '<br/>')}"
        if customer_phone:
            customer_info += f"<br/>Phone: {customer_phone}"
        if customer_email:
            customer_info += f"<br/>Email: {customer_email}"
        elements.append(Paragraph(customer_info, normal_style))
        elements.append(Spacer(1, 0.2*inch))

    # Project name
    if project_name:
        elements.append(Paragraph(f"<b>Project:</b> {project_name}", normal_style))
        elements.append(Spacer(1, 0.2*inch))

    # Line items table
    table_data = [['Code', 'Description', 'Dimensions', 'Qty', 'Unit Price', 'Total']]
    for item in items:
        table_data.append([
            item['code'] or '-',
            item['description'],
            item['dimensions'],
            str(item['quantity']),
            f"${item['unit_price']:,.2f}",
            f"${item['line_total']:,.2f}"
        ])

    # Add empty row if no items
    if not items:
        table_data.append(['-', 'No cabinets added', '-', '-', '-', '-'])

    items_table = Table(table_data, colWidths=[0.8*inch, 2.2*inch, 1.5*inch, 0.5*inch, 1*inch, 1*inch])
    items_table.setStyle(TableStyle([
        # Header row
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#374151')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        # Data rows
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('ALIGN', (3, 1), (3, -1), 'CENTER'),  # Qty centered
        ('ALIGN', (4, 1), (5, -1), 'RIGHT'),   # Prices right-aligned
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ('TOPPADDING', (0, 1), (-1, -1), 6),
        # Grid
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E5E7EB')),
        # Alternating row colors
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F9FAFB')]),
    ]))
    elements.append(items_table)
    elements.append(Spacer(1, 0.2*inch))

    # Totals section
    totals_data = [
        ['', '', '', '', 'Subtotal:', f"${subtotal:,.2f}"],
    ]
    if tax_rate > 0:
        totals_data.append(['', '', '', '', f'Tax ({tax_rate}%):', f"${tax_amount:,.2f}"])
    if shipping > 0:
        totals_data.append(['', '', '', '', 'Shipping:', f"${shipping:,.2f}"])
    totals_data.append(['', '', '', '', 'TOTAL:', f"${grand_total:,.2f}"])

    totals_table = Table(totals_data, colWidths=[0.8*inch, 2.2*inch, 1.5*inch, 0.5*inch, 1*inch, 1*inch])
    totals_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (4, 0), (5, -1), 'RIGHT'),
        ('FONTNAME', (4, -1), (5, -1), 'Helvetica-Bold'),  # Bold total row
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LINEABOVE', (4, -1), (5, -1), 1, colors.HexColor('#374151')),  # Line above total
    ]))
    elements.append(totals_table)
    elements.append(Spacer(1, 0.3*inch))

    # Notes section
    if notes:
        elements.append(Paragraph("<b>Notes:</b>", normal_style))
        elements.append(Paragraph(notes.replace('\n', '<br/>'), notes_style))
        elements.append(Spacer(1, 0.2*inch))

    # Footer
    elements.append(Spacer(1, 0.3*inch))
    elements.append(Paragraph("Thank you for your business!", ParagraphStyle('Footer', parent=styles['Normal'], fontSize=10, alignment=TA_CENTER, textColor=colors.grey)))

    # Build PDF
    doc.build(elements)
    buffer.seek(0)

    # Generate filename
    safe_name = ''.join(c if c.isalnum() or c in '-_' else '-' for c in project_name)
    filename = f"{safe_name}-Quote-{datetime.now().strftime('%Y%m%d')}.pdf"

    return Response(
        buffer.getvalue(),
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )

@app.route('/api/export/packing-list', methods=['POST'])
def export_packing_list():
    """Generate a PDF packing list for job site delivery."""
    state = get_state()
    cabinets = state.get('cabinets', [])
    current_project = state.get('current_project')

    if not cabinets:
        return jsonify({'error': 'No cabinets in project'}), 400

    # Get company info from database
    company_settings = db_manager.get_company_settings() or {}
    company_name = company_settings.get('company_name', 'Cabinet Shop')

    project_name = current_project['name'] if current_project else 'Cabinet Project'

    # Create PDF in memory
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
    elements = []
    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=20, spaceAfter=6, alignment=TA_CENTER)
    header_style = ParagraphStyle('Header', parent=styles['Normal'], fontSize=12, textColor=colors.grey)
    normal_style = ParagraphStyle('CustomNormal', parent=styles['Normal'], fontSize=10)
    bold_style = ParagraphStyle('Bold', parent=styles['Normal'], fontSize=10, fontName='Helvetica-Bold')
    center_style = ParagraphStyle('Center', parent=styles['Normal'], fontSize=10, alignment=TA_CENTER)

    # Header
    elements.append(Paragraph("PACKING LIST", title_style))
    elements.append(Spacer(1, 0.1*inch))

    # Project info
    info_data = [
        [Paragraph(f"<b>Company:</b> {company_name}", normal_style),
         Paragraph(f"<b>Date:</b> {datetime.now().strftime('%B %d, %Y')}", normal_style)],
        [Paragraph(f"<b>Project:</b> {project_name}", normal_style),
         Paragraph(f"<b>Total Items:</b> {sum(c.get('quantity', 1) for c in cabinets)}", normal_style)]
    ]
    info_table = Table(info_data, colWidths=[3.5*inch, 3.5*inch])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 0.3*inch))

    # Cabinet list table - expand each cabinet by quantity for individual line items
    table_data = [['#', 'Code', 'Dimensions (WxHxD)', 'Staged', 'QA', 'Loaded']]

    item_num = 1
    for cab in cabinets:
        dimensions = f"{cab['width']}\"W x {cab['height']}\"H x {cab['depth']}\"D"
        qty = cab.get('quantity', 1)
        code = cab.get('code', '-') or '-'

        # Create a row for each individual cabinet
        for i in range(qty):
            table_data.append([
                str(item_num),
                code,
                dimensions,
                '',  # Staged - empty for initials
                '',  # QA - empty for initials
                ''   # Loaded - empty for initials
            ])
            item_num += 1

    items_table = Table(table_data, colWidths=[0.4*inch, 2.5*inch, 2.5*inch, 0.8*inch, 0.8*inch, 0.8*inch])
    items_table.setStyle(TableStyle([
        # Header row
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#374151')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 11),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('TOPPADDING', (0, 0), (-1, 0), 12),
        # Data rows
        ('FONTSIZE', (0, 1), (-1, -1), 11),
        ('ALIGN', (0, 1), (0, -1), 'CENTER'),  # Item # centered
        ('ALIGN', (3, 1), (5, -1), 'CENTER'),  # Staged, QA, Loaded centered
        ('BOTTOMPADDING', (0, 1), (-1, -1), 10),
        ('TOPPADDING', (0, 1), (-1, -1), 10),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        # Grid
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E5E7EB')),
        # Alternating row colors
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F9FAFB')]),
    ]))
    elements.append(items_table)
    elements.append(Spacer(1, 0.4*inch))

    # Footer with signature lines
    footer_data = [
        ['Packed By: _______________________', 'Date: _______________'],
        ['Received By: _______________________', 'Date: _______________']
    ]
    footer_table = Table(footer_data, colWidths=[3.5*inch, 3*inch])
    footer_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 15),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
    ]))
    elements.append(footer_table)

    # Build PDF
    doc.build(elements)
    buffer.seek(0)

    # Generate filename
    safe_name = ''.join(c if c.isalnum() or c in '-_' else '-' for c in project_name)
    filename = f"{safe_name}-PackingList-{datetime.now().strftime('%Y%m%d')}.pdf"

    return Response(
        buffer.getvalue(),
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )

@app.route('/api/export/labels', methods=['POST'])
def export_labels():
    """Generate a PDF sheet with cabinet labels (10 per page, 4x2 inches each)."""
    state = get_state()
    cabinets = state.get('cabinets', [])
    current_project = state.get('current_project')

    if not cabinets:
        return jsonify({'error': 'No cabinets in project'}), 400

    project_name = current_project['name'] if current_project else 'Cabinet Project'

    # Expand cabinets by quantity to get all labels
    all_labels = []
    for cab in cabinets:
        qty = cab.get('quantity', 1)
        for i in range(qty):
            all_labels.append({
                'code': cab.get('code', '') or f"CAB-{len(all_labels)+1}",
                'type': cab.get('type', 'Cabinet').replace(' Cabinets', '').replace(' Cabinet', ''),
                'dimensions': f"{cab['width']}\"W x {cab['height']}\"H x {cab['depth']}\"D",
                'project': project_name
            })

    total_labels = len(all_labels)

    # Label layout constants for OL125 template (in points, 72 points = 1 inch)
    # OL125: 4" x 2" labels, 2 columns x 5 rows, 0.25" side margins, 0.5" top margin
    PAGE_WIDTH, PAGE_HEIGHT = letter  # 612 x 792 points (8.5" x 11")
    LABEL_WIDTH = 4 * inch
    LABEL_HEIGHT = 2 * inch
    MARGIN_LEFT = 0.25 * inch
    MARGIN_TOP = 0.5 * inch
    GUTTER_H = 0  # no horizontal gap - labels touch
    GUTTER_V = 0  # no vertical gap - labels touch
    LABELS_PER_ROW = 2
    LABELS_PER_COL = 5
    LABELS_PER_PAGE = LABELS_PER_ROW * LABELS_PER_COL

    # Create PDF with canvas for precise positioning
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)

    for idx, label in enumerate(all_labels):
        # Calculate position on page
        page_label_idx = idx % LABELS_PER_PAGE
        col = page_label_idx % LABELS_PER_ROW
        row = page_label_idx // LABELS_PER_ROW

        # Calculate x, y (bottom-left of label)
        x = MARGIN_LEFT + col * (LABEL_WIDTH + GUTTER_H)
        y = PAGE_HEIGHT - MARGIN_TOP - (row + 1) * LABEL_HEIGHT - row * GUTTER_V

        # Label content - padded 15pt from edges for rounded corners
        PADDING = 15

        # Draw dotted border inside label margins
        c.setStrokeColor(colors.HexColor('#CCCCCC'))
        c.setDash(3, 3)
        c.rect(x + 5, y + 5, LABEL_WIDTH - 10, LABEL_HEIGHT - 10)
        c.setDash()  # Reset to solid

        # Code (large, bold, top)
        c.setFont('Helvetica-Bold', 22)
        c.setFillColor(colors.black)
        code_text = label['code'][:25]  # Truncate if too long
        c.drawString(x + PADDING, y + LABEL_HEIGHT - 30, code_text)

        # Cabinet type
        c.setFont('Helvetica', 14)
        c.setFillColor(colors.black)
        c.drawString(x + PADDING, y + LABEL_HEIGHT - 55, label['type'])

        # Dimensions
        c.setFont('Helvetica', 14)
        c.drawString(x + PADDING, y + LABEL_HEIGHT - 75, label['dimensions'])

        # Project name (bottom left, smaller, gray)
        c.setFont('Helvetica-Oblique', 10)
        c.setFillColor(colors.HexColor('#666666'))
        project_text = label['project'][:35]  # Truncate if too long
        c.drawString(x + PADDING, y + PADDING, project_text)

        # Sequence number (bottom right)
        c.setFont('Helvetica', 11)
        c.setFillColor(colors.HexColor('#666666'))
        seq_text = f"{idx + 1}/{total_labels}"
        c.drawRightString(x + LABEL_WIDTH - PADDING, y + PADDING, seq_text)

        # Start new page if needed
        if page_label_idx == LABELS_PER_PAGE - 1 and idx < total_labels - 1:
            c.showPage()

    c.save()
    buffer.seek(0)

    # Generate filename
    safe_name = ''.join(c if c.isalnum() or c in '-_' else '-' for c in project_name)
    filename = f"{safe_name}-Labels-{datetime.now().strftime('%Y%m%d')}.pdf"

    return Response(
        buffer.getvalue(),
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )

@app.route('/api/export/csv')
def export_csv():
    """Export current project cabinets to CSV."""
    state = get_state()
    cabinets = state.get('cabinets', [])
    calc_params = state.get('calc_params', DEFAULT_CALC_PARAMS)
    current_project = state.get('current_project')

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Header row
    writer.writerow([
        'Code', 'Type', 'Width', 'Height', 'Depth', 'Quantity',
        'Doors', 'Drawers', 'Shelves',
        'Primary Substrate (sqft)', 'Back Panel (sqft)', 'Drawer Material (sqft)', 'Door/Drawer Fronts (sqft)',
        'Total Cost'
    ])

    # Calculate and write cabinet data
    total_primary = 0
    total_back = 0
    total_drawer = 0
    total_fronts = 0
    total_cost = 0

    for cab in cabinets:
        components = calculate_cabinet_components(
            cab['type'], cab['width'], cab['height'], cab['depth'],
            cab['has_shelves'], cab['num_shelves'],
            cab['has_drawers'], cab['num_drawers'],
            cab.get('has_false_drawers', False), cab.get('num_false_drawers', 0),
            cab.get('has_dividers', False), cab.get('num_dividers', 0),
            cab.get('has_pullout_shelves', False), cab.get('num_pullout_shelves', 0),
            cab.get('use_axial_drawers', True),
            cab['has_doors'], cab['num_doors'],
            calc_params,
            cab.get('panel_sides', 0)
        )
        hardware = calculate_hardware(
            cab['type'], cab['has_doors'], cab['num_doors'],
            cab['has_drawers'], cab['num_drawers'],
            cab['has_shelves'], cab['num_shelves'],
            cab.get('has_false_drawers', False), cab.get('num_false_drawers', 0),
            cab.get('has_dividers', False), cab.get('num_dividers', 0),
            cab.get('has_pullout_shelves', False), cab.get('num_pullout_shelves', 0),
            cab.get('use_axial_drawers', True)
        )
        mat, hw, eb = calculate_costs(
            components, hardware, cab.get('quantity', 1),
            cab['type'], cab.get('edgebanding_type', '1.0mm PVC'),
            calc_params
        )
        cab_total = mat * 1.1 + hw + eb

        # Calculate square feet by material type
        quantity = cab.get('quantity', 1)
        sqft_primary = 0
        sqft_back = 0
        sqft_drawer = 0
        sqft_fronts = 0

        for comp_name, comp_data in components.items():
            sqft = calculate_sq_feet(comp_data["length"], comp_data["height"]) * comp_data["qty"] * quantity
            material = comp_data["material"]
            if material == "Primary Substrate":
                sqft_primary += sqft
            elif material == "Back Panel":
                sqft_back += sqft
            elif material == "Drawer Material":
                sqft_drawer += sqft
            elif material == "Door/Drawer Fronts":
                sqft_fronts += sqft

        total_primary += sqft_primary
        total_back += sqft_back
        total_drawer += sqft_drawer
        total_fronts += sqft_fronts
        total_cost += cab_total

        writer.writerow([
            cab.get('code', ''),
            cab['type'],
            cab['width'],
            cab['height'],
            cab['depth'],
            cab.get('quantity', 1),
            cab['num_doors'] if cab['has_doors'] else 0,
            cab['num_drawers'] if cab['has_drawers'] else 0,
            cab['num_shelves'] if cab['has_shelves'] else 0,
            f"{sqft_primary:.2f}",
            f"{sqft_back:.2f}",
            f"{sqft_drawer:.2f}",
            f"{sqft_fronts:.2f}",
            f"${cab_total:.2f}"
        ])

    # Add empty row and totals
    writer.writerow([])
    writer.writerow(['', '', '', '', '', '', '', '', 'TOTALS:',
                     f"{total_primary:.2f}", f"{total_back:.2f}",
                     f"{total_drawer:.2f}", f"{total_fronts:.2f}",
                     f"${total_cost:.2f}"])

    # Generate filename
    project_name = current_project['name'] if current_project else 'cabinet-quote'
    safe_name = "".join(c for c in project_name if c.isalnum() or c in (' ', '-', '_')).strip()
    filename = f"{safe_name}-{datetime.now().strftime('%Y%m%d')}.csv"

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )

@app.route('/api/export/csv/complex/<int:project_id>')
def export_complex_csv(project_id):
    """Export apartment complex project to CSV with all units and configurations."""
    project = db_manager.load_project_with_type(project_id)
    if not project:
        return "Project not found", 404
    if project.get('project_type') != 'apartment_complex':
        return "Not a multi unit project", 400

    data = project.get('data', {'units': []})
    calc_params = project.get('calc_params', DEFAULT_CALC_PARAMS)
    templates = {t['id']: t for t in db_manager.get_all_kitchen_templates(project_id)}
    standard_cabinets = {c['id']: c for c in db_manager.get_all_standard_cabinets(project_id)}

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Header row
    writer.writerow([
        'Unit', 'Item Type', 'Item Name', 'Code', 'Cabinet Type',
        'Width', 'Height', 'Depth', 'Quantity',
        'Doors', 'Drawers', 'Shelves',
        'Primary Substrate (sqft)', 'Back Panel (sqft)', 'Drawer Material (sqft)', 'Door/Drawer Fronts (sqft)',
        'Item Cost'
    ])

    # Track totals
    grand_total_primary = 0
    grand_total_back = 0
    grand_total_drawer = 0
    grand_total_fronts = 0
    grand_total_cost = 0

    # Process each unit
    for unit in data.get('units', []):
        unit_number = unit.get('unit_number', 'Unknown')
        items = unit.get('items', [])

        for item in items:
            item_type = item.get('type', 'unknown')
            item_name = ''
            cabinets_to_process = []

            if item_type == 'template':
                template_id = item.get('template_id')
                template = templates.get(template_id) or db_manager.get_kitchen_template(template_id)
                item_name = template.get('name', 'Unknown Template') if template else 'Unknown Template'
                if template:
                    template_cabs = template.get('cabinets', [])
                    item_qty = item.get('quantity', 1)
                    for cab in template_cabs:
                        cab_copy = cab.copy()
                        cab_copy['_item_qty'] = item_qty
                        cabinets_to_process.append(cab_copy)

            elif item_type == 'standard':
                std_id = item.get('standard_cabinet_id')
                std_cab = standard_cabinets.get(std_id) or db_manager.get_standard_cabinet(std_id)
                item_name = std_cab.get('name', 'Unknown') if std_cab else 'Unknown'
                if std_cab:
                    cab_copy = std_cab.copy()
                    cab_copy['quantity'] = item.get('quantity', 1)
                    cabinets_to_process.append(cab_copy)

            elif item_type == 'custom':
                item_name = item.get('code') or item.get('cabinet_type', 'Custom')
                cab_copy = item.copy()
                cab_copy['type'] = item.get('cabinet_type', item.get('type', 'Base Cabinets'))
                cabinets_to_process.append(cab_copy)

            # Process cabinets for this item
            for cab in cabinets_to_process:
                cab_type = cab.get('type', 'Base Cabinets')
                width = cab.get('width', 24)
                height = cab.get('height', 34.5)
                depth = cab.get('depth', 24)
                cab_qty = cab.get('quantity', 1)
                item_multiplier = cab.get('_item_qty', 1)
                total_qty = cab_qty * item_multiplier

                num_doors = cab.get('num_doors', 0)
                num_drawers = cab.get('num_drawers', 0)
                num_shelves = cab.get('num_shelves', 0)
                has_doors = cab.get('has_doors', num_doors > 0)
                has_drawers = cab.get('has_drawers', num_drawers > 0)
                has_shelves = cab.get('has_shelves', num_shelves > 0)

                # Calculate components
                components = calculate_cabinet_components(
                    cab_type, width, height, depth,
                    has_shelves, num_shelves,
                    has_drawers, num_drawers,
                    cab.get('has_false_drawers', False), cab.get('num_false_drawers', 0),
                    cab.get('has_dividers', False), cab.get('num_dividers', 0),
                    cab.get('has_pullout_shelves', False), cab.get('num_pullout_shelves', 0),
                    cab.get('use_axial_drawers', True),
                    has_doors, num_doors,
                    calc_params,
                    cab.get('panel_sides', 0)
                )
                hardware = calculate_hardware(
                    cab_type, has_doors, num_doors,
                    has_drawers, num_drawers,
                    has_shelves, num_shelves,
                    cab.get('has_false_drawers', False), cab.get('num_false_drawers', 0),
                    cab.get('has_dividers', False), cab.get('num_dividers', 0),
                    cab.get('has_pullout_shelves', False), cab.get('num_pullout_shelves', 0),
                    cab.get('use_axial_drawers', True)
                )
                mat, hw, eb = calculate_costs(
                    components, hardware, total_qty,
                    cab_type, cab.get('edgebanding_type', '1.0mm PVC'),
                    calc_params
                )
                cab_total = mat * 1.1 + hw + eb

                # Calculate square feet by material type
                sqft_primary = 0
                sqft_back = 0
                sqft_drawer = 0
                sqft_fronts = 0

                for comp_name, comp_data in components.items():
                    sqft = calculate_sq_feet(comp_data["length"], comp_data["height"]) * comp_data["qty"] * total_qty
                    material = comp_data["material"]
                    if material == "Primary Substrate":
                        sqft_primary += sqft
                    elif material == "Back Panel":
                        sqft_back += sqft
                    elif material == "Drawer Material":
                        sqft_drawer += sqft
                    elif material == "Door/Drawer Fronts":
                        sqft_fronts += sqft

                grand_total_primary += sqft_primary
                grand_total_back += sqft_back
                grand_total_drawer += sqft_drawer
                grand_total_fronts += sqft_fronts
                grand_total_cost += cab_total

                writer.writerow([
                    unit_number,
                    item_type.capitalize(),
                    item_name,
                    cab.get('code', ''),
                    cab_type,
                    width,
                    height,
                    depth,
                    total_qty,
                    num_doors if has_doors else 0,
                    num_drawers if has_drawers else 0,
                    num_shelves if has_shelves else 0,
                    f"{sqft_primary:.2f}",
                    f"{sqft_back:.2f}",
                    f"{sqft_drawer:.2f}",
                    f"{sqft_fronts:.2f}",
                    f"${cab_total:.2f}"
                ])

    # Add empty row and totals
    writer.writerow([])
    writer.writerow(['', '', '', '', '', '', '', '', '', '', '', 'TOTALS:',
                     f"{grand_total_primary:.2f}", f"{grand_total_back:.2f}",
                     f"{grand_total_drawer:.2f}", f"{grand_total_fronts:.2f}",
                     f"${grand_total_cost:.2f}"])

    # Generate filename
    safe_name = "".join(c for c in project['name'] if c.isalnum() or c in (' ', '-', '_')).strip()
    filename = f"{safe_name}-{datetime.now().strftime('%Y%m%d')}.csv"

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


# ============================================================================
# AI ASSISTANT API
# ============================================================================

@app.route('/api/ai/chat', methods=['POST'])
def ai_chat():
    """Handle AI assistant chat messages."""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'No data provided'})

    message = data.get('message', '').strip()
    context = data.get('context', {})

    if not message:
        return jsonify({'success': False, 'message': 'No message provided'})

    # Get available templates and standard cabinets for AI context
    ai_project_id = context.get('project_id')
    templates = db_manager.get_all_kitchen_templates(ai_project_id)
    standard_cabinets = db_manager.get_all_standard_cabinets(ai_project_id)

    # Process the command through AI
    result = ai_process_command(message, context, templates, standard_cabinets)

    # If an action is needed, execute it
    if result.get('success') and result.get('data'):
        action = result.get('action')
        action_data = result.get('data')
        page = context.get('page', '')
        project_id = context.get('project_id')
        unit_id = context.get('unit_id')

        try:
            if action == 'create_unit' and project_id:
                # Create a new unit
                unit_number = action_data.get('unit_number', 'New Unit')
                project = db_manager.load_project_with_type(project_id)
                if project and project.get('project_type') == 'apartment_complex':
                    proj_data = project.get('data', {'units': []})
                    new_unit = {
                        'id': str(uuid.uuid4()),
                        'unit_number': unit_number,
                        'items': []
                    }
                    proj_data['units'].append(new_unit)
                    db_manager.update_apartment_complex(
                        project_id, project['name'], proj_data,
                        project.get('calc_params', DEFAULT_CALC_PARAMS)
                    )
                    result['message'] = f"Created unit {unit_number}"
                    result['created_unit_id'] = new_unit['id']
                else:
                    result['success'] = False
                    result['message'] = "Can only create units in multi unit projects"

            elif action in ['create_units_batch', 'create_units_list'] and project_id:
                # Create multiple units at once
                unit_numbers = action_data.get('unit_numbers', [])
                project = db_manager.load_project_with_type(project_id)
                if project and project.get('project_type') == 'apartment_complex':
                    proj_data = project.get('data', {'units': []})
                    created_count = 0
                    for unit_number in unit_numbers:
                        new_unit = {
                            'id': str(uuid.uuid4()),
                            'unit_number': str(unit_number),
                            'items': []
                        }
                        proj_data['units'].append(new_unit)
                        created_count += 1
                    db_manager.update_apartment_complex(
                        project_id, project['name'], proj_data,
                        project.get('calc_params', DEFAULT_CALC_PARAMS)
                    )
                    if created_count <= 5:
                        result['message'] = f"Created {created_count} units: {', '.join(str(u) for u in unit_numbers)}"
                    else:
                        result['message'] = f"Created {created_count} units: {unit_numbers[0]} ... {unit_numbers[-1]}"
                else:
                    result['success'] = False
                    result['message'] = "Can only create units in multi unit projects"

            elif action in ['add_template', 'add_standard', 'add_custom'] and project_id and unit_id:
                # Add item to unit
                project = db_manager.load_project_with_type(project_id)
                if project and project.get('project_type') == 'apartment_complex':
                    proj_data = project.get('data', {'units': []})
                    unit_found = False

                    for unit in proj_data.get('units', []):
                        if unit.get('id') == unit_id:
                            unit_found = True
                            items = unit.get('items', [])
                            new_item = {'id': str(uuid.uuid4())}
                            new_item.update(action_data)
                            items.append(new_item)
                            unit['items'] = items
                            break

                    if unit_found:
                        db_manager.update_apartment_complex(
                            project_id, project['name'], proj_data,
                            project.get('calc_params', DEFAULT_CALC_PARAMS)
                        )
                        result['message'] = f"Added item to unit"
                    else:
                        result['success'] = False
                        result['message'] = "Unit not found"
                else:
                    result['success'] = False
                    result['message'] = "Project not found or not a multi unit project"

            elif action == 'add_template_to_units' and project_id:
                # Add template to multiple units by unit number
                unit_numbers = action_data.get('unit_numbers', [])
                template_id = action_data.get('template_id')
                template_name = action_data.get('template_name', 'template')
                quantity = action_data.get('quantity', 1)

                project = db_manager.load_project_with_type(project_id)
                if project and project.get('project_type') == 'apartment_complex':
                    proj_data = project.get('data', {'units': []})
                    updated_count = 0

                    for unit in proj_data.get('units', []):
                        if unit.get('unit_number') in unit_numbers:
                            items = unit.get('items', [])
                            new_item = {
                                'id': str(uuid.uuid4()),
                                'type': 'template',
                                'template_id': template_id,
                                'quantity': quantity
                            }
                            items.append(new_item)
                            unit['items'] = items
                            updated_count += 1

                    if updated_count > 0:
                        db_manager.update_apartment_complex(
                            project_id, project['name'], proj_data,
                            project.get('calc_params', DEFAULT_CALC_PARAMS)
                        )
                        result['message'] = f"Added {template_name} to {updated_count} units"
                    else:
                        result['success'] = False
                        result['message'] = f"No matching units found for: {', '.join(unit_numbers[:5])}..."
                        result['needs_refresh'] = False
                else:
                    result['success'] = False
                    result['message'] = "Project not found or not a multi unit project"

            elif action in ['add_template', 'add_standard', 'add_custom'] and not unit_id:
                # User is not in a unit context
                result['success'] = False
                result['message'] = "Please navigate to a unit first to add items, or create a new unit."
                result['needs_refresh'] = False

        except Exception as e:
            result['success'] = False
            result['message'] = f"Error executing action: {str(e)}"
            result['needs_refresh'] = False

    return jsonify(result)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
