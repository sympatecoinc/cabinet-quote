-- PostgreSQL schema for Cabinet Quoter

CREATE TABLE IF NOT EXISTS projects (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    project_type TEXT DEFAULT 'standard',
    cabinets TEXT DEFAULT '[]',
    data TEXT,
    calc_params TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kitchen_templates (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    cabinets TEXT DEFAULT '[]',
    project_id INTEGER,
    is_global INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS standard_cabinets (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    code TEXT DEFAULT '',
    type TEXT DEFAULT 'Base Cabinets',
    width REAL DEFAULT 24,
    height REAL DEFAULT 34.5,
    depth REAL DEFAULT 24,
    has_doors INTEGER DEFAULT 0,
    num_doors INTEGER DEFAULT 0,
    has_drawers INTEGER DEFAULT 0,
    num_drawers INTEGER DEFAULT 0,
    has_shelves INTEGER DEFAULT 0,
    num_shelves INTEGER DEFAULT 0,
    has_false_drawers INTEGER DEFAULT 0,
    num_false_drawers INTEGER DEFAULT 0,
    has_dividers INTEGER DEFAULT 0,
    num_dividers INTEGER DEFAULT 0,
    has_pullout_shelves INTEGER DEFAULT 0,
    num_pullout_shelves INTEGER DEFAULT 0,
    use_axial_drawers INTEGER DEFAULT 1,
    edgebanding_type TEXT DEFAULT '1.0mm PVC',
    project_id INTEGER,
    is_global INTEGER DEFAULT 0,
    panel_sides INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pricing_rules (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    markup_primary REAL DEFAULT 0,
    markup_back REAL DEFAULT 0,
    markup_door_drawer REAL DEFAULT 0,
    markup_drawer_material REAL DEFAULT 0,
    markup_hardware REAL DEFAULT 0,
    markup_edgebanding REAL DEFAULT 0,
    material_usage_buffer REAL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS company_settings (
    id SERIAL PRIMARY KEY,
    company_name TEXT DEFAULT '',
    company_address TEXT DEFAULT '',
    company_phone TEXT DEFAULT '',
    company_email TEXT DEFAULT '',
    updated_at TEXT NOT NULL
);
