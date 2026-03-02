"""
AI Assistant module for Cabinet Quoter
Uses Claude API to parse natural language commands
"""

import anthropic
import os
import json
import re

CLAUDE_MODEL = os.environ.get('CLAUDE_MODEL', 'claude-haiku-4-5-20251001')

# Cabinet type defaults for reference
CABINET_DEFAULTS = {
    'Wall Cabinets': {'depth': 12, 'height': 30, 'doors': 1, 'drawers': 0, 'shelves': 0},
    'Base Cabinets': {'depth': 24, 'height': 34.5, 'doors': 1, 'drawers': 0, 'shelves': 0},
    'Open Base Cabinets': {'depth': 24, 'height': 34.5, 'doors': 0, 'drawers': 0, 'shelves': 1},
    'Drawer Cabinets': {'depth': 24, 'height': 34.5, 'doors': 0, 'drawers': 4, 'shelves': 0},
    'Tall Cabinets': {'depth': 24, 'height': 84, 'doors': 2, 'drawers': 0, 'shelves': 0},
    'Sink Base Cabinets': {'depth': 24, 'height': 34.5, 'doors': 2, 'drawers': 0, 'shelves': 0},
    'Pull Out Trashcan': {'depth': 24, 'height': 35, 'doors': 0, 'drawers': 0, 'shelves': 0},
    'Appliance Panel': {'depth': 24, 'height': 34.5, 'doors': 0, 'drawers': 0, 'shelves': 0}
}


def build_system_prompt(templates=None, standard_cabinets=None):
    """Build the system prompt with available templates and cabinets."""

    template_list = ""
    if templates:
        template_list = ", ".join([f'"{t["name"]}"' for t in templates])

    standard_list = ""
    if standard_cabinets:
        standard_list = ", ".join([f'"{s.get("code", s["name"])}"' for s in standard_cabinets])

    return f"""You are an AI assistant for a cabinet quoting app. Parse commands into JSON actions.

AVAILABLE TEMPLATES: {template_list if template_list else "none"}
AVAILABLE STANDARD CABINETS: {standard_list if standard_list else "none"}

CABINET TYPES: Wall Cabinets, Base Cabinets, Open Base Cabinets, Drawer Cabinets, Tall Cabinets, Sink Base Cabinets

RESPOND WITH ONLY A JSON OBJECT. No other text. Use these actions:

1. CREATE SINGLE UNIT: {{"action": "create_unit", "params": {{"unit_number": "NAME"}}, "message": "Creating unit NAME"}}
2. CREATE MULTIPLE UNITS (range): {{"action": "create_units_batch", "params": {{"start": NUM, "end": NUM, "prefix": ""}}, "message": "Creating units NUM to NUM"}}
3. CREATE MULTIPLE NAMED UNITS (list): {{"action": "create_units_list", "params": {{"unit_names": ["NAME1", "NAME2", "NAME3"]}}, "message": "Creating units NAME1, NAME2, NAME3"}}
4. ADD TEMPLATE (single unit): {{"action": "add_template", "params": {{"template_name": "NAME"}}, "message": "Adding template NAME"}}
5. ADD TEMPLATE TO MULTIPLE UNITS: {{"action": "add_template_to_units", "params": {{"template_name": "NAME", "start": NUM, "end": NUM, "prefix": ""}}, "message": "Adding template to units"}}
6. ADD CUSTOM CABINET: {{"action": "add_custom", "params": {{"cabinet_type": "TYPE", "width": NUM, "quantity": NUM}}, "message": "Adding cabinet"}}
7. ADD STANDARD: {{"action": "add_standard", "params": {{"standard_name": "CODE"}}, "message": "Adding standard CODE"}}
8. LIST TEMPLATES: {{"action": "list_templates", "params": {{}}, "message": "Listing templates"}}
9. LIST STANDARDS: {{"action": "list_standards", "params": {{}}, "message": "Listing standards"}}
10. INFO/HELP: {{"action": "info", "params": {{}}, "message": "Your helpful response"}}

NOTE: The word "unit" or "units" is OPTIONAL. Users can reference units directly by number like "103-110" or "1A".

IMPORTANT FOR UNIT LISTS: When parsing comma-separated unit names, split ONLY on commas. Unit names CAN contain spaces (e.g., "3C ADA" is ONE unit name, not two). Preserve the exact text between commas including any spaces.

EXAMPLES:
- "create 1A" -> {{"action": "create_unit", "params": {{"unit_number": "1A"}}, "message": "Creating unit 1A"}}
- "create unit 1A" -> {{"action": "create_unit", "params": {{"unit_number": "1A"}}, "message": "Creating unit 1A"}}
- "create 102 to 110" -> {{"action": "create_units_batch", "params": {{"start": 102, "end": 110, "prefix": ""}}, "message": "Creating units 102 to 110"}}
- "create 102-110" -> {{"action": "create_units_batch", "params": {{"start": 102, "end": 110, "prefix": ""}}, "message": "Creating units 102 to 110"}}
- "create A1 to A10" -> {{"action": "create_units_batch", "params": {{"start": 1, "end": 10, "prefix": "A"}}, "message": "Creating units A1 to A10"}}
- "add units 1+ ADA, 1+, 1A, 2+A, 2+B" -> {{"action": "create_units_list", "params": {{"unit_names": ["1+ ADA", "1+", "1A", "2+A", "2+B"]}}, "message": "Creating 5 units"}}
- "create units 1A, 1B, 2A, 2B, 3A" -> {{"action": "create_units_list", "params": {{"unit_names": ["1A", "1B", "2A", "2B", "3A"]}}, "message": "Creating 5 units"}}
- "add 101, 102A, 103B, 104" -> {{"action": "create_units_list", "params": {{"unit_names": ["101", "102A", "103B", "104"]}}, "message": "Creating 4 units"}}
- "add units 3A, 3B, 3C ADA" -> {{"action": "create_units_list", "params": {{"unit_names": ["3A", "3B", "3C ADA"]}}, "message": "Creating 3 units"}}
- "add kitchen E" -> {{"action": "add_template", "params": {{"template_name": "Kitchen E"}}, "message": "Adding Kitchen E template"}}
- "add kitchen E to 103-110" -> {{"action": "add_template_to_units", "params": {{"template_name": "Kitchen E", "start": 103, "end": 110, "prefix": ""}}, "message": "Adding Kitchen E to 103-110"}}
- "add kitchen E to 103 to 110" -> {{"action": "add_template_to_units", "params": {{"template_name": "Kitchen E", "start": 103, "end": 110, "prefix": ""}}, "message": "Adding Kitchen E to 103-110"}}
- "add kitchen type E to A1-A5" -> {{"action": "add_template_to_units", "params": {{"template_name": "Kitchen E", "start": 1, "end": 5, "prefix": "A"}}, "message": "Adding Kitchen E to A1-A5"}}
- "add a base cabinet 18 wide" -> {{"action": "add_custom", "params": {{"cabinet_type": "Base Cabinets", "width": 18}}, "message": "Adding base cabinet 18\\" wide"}}
- "add 2 wall cabinets" -> {{"action": "add_custom", "params": {{"cabinet_type": "Wall Cabinets", "quantity": 2}}, "message": "Adding 2 wall cabinets"}}
- "what templates?" -> {{"action": "list_templates", "params": {{}}, "message": "Here are the available templates"}}

RESPOND WITH ONLY THE JSON OBJECT."""


def chat_with_claude(message, system_prompt):
    """Send a message to Claude API and get a response."""
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": message}]
        )
        return response.content[0].text
    except Exception as e:
        print(f"Claude API request error: {e}")
        return None


def fix_json_string(json_str):
    """Fix common JSON errors from LLMs."""
    # Remove trailing extra braces (common LLM mistake)
    while json_str.endswith('}}'):
        # Count opening and closing braces
        open_count = json_str.count('{')
        close_count = json_str.count('}')
        if close_count > open_count:
            json_str = json_str[:-1]
        else:
            break
    return json_str


def parse_ai_response(response_text):
    """Parse the AI response into a structured intent."""
    if not response_text:
        return {
            'action': 'error',
            'params': {},
            'message': 'Failed to get response from AI'
        }

    # Try to extract JSON from the response
    try:
        # First, try direct parse
        return json.loads(response_text.strip())
    except json.JSONDecodeError:
        pass

    # Try to find JSON in the response (sometimes LLMs add extra text)
    json_match = re.search(r'\{[\s\S]*\}', response_text)
    if json_match:
        json_str = fix_json_string(json_match.group())
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    # Fallback - return the raw response as info
    return {
        'action': 'info',
        'params': {},
        'message': response_text
    }


def normalize_name(name):
    """Normalize a name by removing special characters and extra spaces."""
    # Remove quotes, dashes, underscores, and other special chars
    normalized = re.sub(r"['\"\-_\(\)\[\]]", " ", name.lower())
    # Collapse multiple spaces
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


def extract_identifier(name):
    """Extract key identifier (like 'E' from 'Kitchen E' or 'Kitchen 'E'')."""
    # Look for a single letter or number identifier at the end
    match = re.search(r'[^a-zA-Z0-9]([a-zA-Z0-9]{1,3})[\s\'\"]*$', name)
    if match:
        return match.group(1).lower()
    # Also try just the last word
    parts = normalize_name(name).split()
    if parts:
        return parts[-1]
    return name.lower()


def find_template_by_name(templates, name):
    """Find a template by name (flexible matching - ignores quotes, case, special chars)."""
    if not templates or not name:
        return None

    name_normalized = normalize_name(name)
    name_identifier = extract_identifier(name)

    # Get the base part (e.g., "kitchen" from "kitchen e")
    name_parts = name_normalized.split()
    name_base = ' '.join(name_parts[:-1]) if len(name_parts) > 1 else ''

    # First try exact normalized match
    for t in templates:
        if normalize_name(t['name']) == name_normalized:
            return t

    # Try matching by identifier (e.g., "kitchen e" matches "Kitchen 'E'")
    # BUT require at least some base name context (not just "e" alone)
    if name_base:  # Only do identifier matching if there's a base name
        for t in templates:
            t_identifier = extract_identifier(t['name'])
            t_normalized = normalize_name(t['name'])
            # Remove identifier from end only (not all occurrences)
            t_parts = t_normalized.split()
            t_base = ' '.join(t_parts[:-1]) if len(t_parts) > 1 else t_normalized

            # Check if identifiers match AND base names have word overlap
            if name_identifier == t_identifier:
                # Check if any significant word from user input appears in template base
                name_words = [w for w in name_base.split() if len(w) >= 3]
                t_words = t_base.split()
                for nw in name_words:
                    for tw in t_words:
                        # Check if words match or one contains the other (bath/bathroom)
                        if nw in tw or tw in nw:
                            return t

    # Then try partial match on normalized names (requires reasonable overlap)
    for t in templates:
        t_normalized = normalize_name(t['name'])
        # Require at least 3 chars or multiple words to match partially
        if len(name_normalized) >= 3:
            if name_normalized in t_normalized or t_normalized in name_normalized:
                return t

    return None


def find_standard_by_name(standards, name):
    """Find a standard cabinet by name or code (flexible matching)."""
    if not standards or not name:
        return None

    name_normalized = normalize_name(name)

    # First try exact match on code (case-insensitive)
    for s in standards:
        if s.get('code', '').lower() == name_normalized:
            return s

    # Then try normalized match on name
    for s in standards:
        if normalize_name(s['name']) == name_normalized:
            return s

    # Then try partial match on code or normalized name
    for s in standards:
        code = s.get('code', '').lower()
        sname = normalize_name(s['name'])
        if name_normalized in code or name_normalized in sname or code in name_normalized or sname in name_normalized:
            return s

    return None


def process_command(message, context, templates=None, standard_cabinets=None):
    """
    Process a user command and return the action to take.

    Args:
        message: User's natural language message
        context: Dict with page, project_id, unit_id info
        templates: List of available kitchen templates
        standard_cabinets: List of available standard cabinets

    Returns:
        Dict with action, params, message, and success status
    """

    # Build the system prompt with current data
    system_prompt = build_system_prompt(templates, standard_cabinets)

    # Get AI response
    ai_response = chat_with_claude(message, system_prompt)

    if not ai_response:
        return {
            'success': False,
            'action': 'error',
            'params': {},
            'message': 'Could not connect to AI assistant. Check ANTHROPIC_API_KEY.',
            'needs_refresh': False
        }

    # Parse the response
    intent = parse_ai_response(ai_response)
    action = intent.get('action', 'error')
    params = intent.get('params', {})
    ai_message = intent.get('message', '')

    result = {
        'success': True,
        'action': action,
        'params': params,
        'message': ai_message,
        'needs_refresh': False,
        'data': None
    }

    # Process based on action type
    if action == 'add_template':
        template_name = params.get('template_name', '')
        template = find_template_by_name(templates, template_name)
        if template:
            result['data'] = {
                'type': 'template',
                'template_id': template['id'],
                'quantity': params.get('quantity', 1)
            }
            result['needs_refresh'] = True
        else:
            result['success'] = False
            result['message'] = f"Could not find template matching '{template_name}'"

    elif action == 'add_standard':
        standard_name = params.get('standard_name', '')
        standard = find_standard_by_name(standard_cabinets, standard_name)
        if standard:
            result['data'] = {
                'type': 'standard',
                'standard_cabinet_id': standard['id'],
                'quantity': params.get('quantity', 1)
            }
            result['needs_refresh'] = True
        else:
            result['success'] = False
            result['message'] = f"Could not find standard cabinet matching '{standard_name}'"

    elif action == 'add_custom':
        cabinet_type = params.get('cabinet_type', 'Base Cabinets')
        defaults = CABINET_DEFAULTS.get(cabinet_type, CABINET_DEFAULTS['Base Cabinets'])

        result['data'] = {
            'type': 'custom',
            'cabinet_type': cabinet_type,
            'width': params.get('width', 24),
            'height': params.get('height', defaults['height']),
            'depth': params.get('depth', defaults['depth']),
            'quantity': params.get('quantity', 1),
            'has_doors': params.get('doors', defaults['doors']) > 0,
            'num_doors': params.get('doors', defaults['doors']),
            'has_drawers': params.get('drawers', defaults['drawers']) > 0,
            'num_drawers': params.get('drawers', defaults['drawers']),
            'has_shelves': params.get('shelves', defaults['shelves']) > 0,
            'num_shelves': params.get('shelves', defaults['shelves']),
            'edgebanding_type': params.get('edgebanding', '1.0mm PVC')
        }
        result['needs_refresh'] = True

    elif action == 'create_unit':
        result['data'] = {
            'unit_number': params.get('unit_number', 'New Unit')
        }
        result['needs_refresh'] = True

    elif action == 'create_units_batch':
        start = params.get('start', 1)
        end = params.get('end', 1)
        prefix = params.get('prefix', '')

        # Generate list of unit numbers
        unit_numbers = []
        for i in range(int(start), int(end) + 1):
            unit_numbers.append(f"{prefix}{i}")

        result['data'] = {
            'unit_numbers': unit_numbers
        }
        result['message'] = f"Creating {len(unit_numbers)} units: {unit_numbers[0]} to {unit_numbers[-1]}"
        result['needs_refresh'] = True

    elif action == 'create_units_list':
        # Create units from a list of specific names
        unit_names = params.get('unit_names', [])
        if unit_names:
            result['data'] = {
                'unit_numbers': unit_names
            }
            result['message'] = f"Creating {len(unit_names)} units: {', '.join(unit_names)}"
            result['needs_refresh'] = True
        else:
            result['success'] = False
            result['message'] = "No unit names provided"

    elif action == 'add_template_to_units':
        template_name = params.get('template_name', '')
        template = find_template_by_name(templates, template_name)
        if template:
            start = params.get('start', 1)
            end = params.get('end', 1)
            prefix = params.get('prefix', '')

            # Generate list of unit numbers to target
            unit_numbers = []
            for i in range(int(start), int(end) + 1):
                unit_numbers.append(f"{prefix}{i}")

            result['data'] = {
                'type': 'template',
                'template_id': template['id'],
                'template_name': template['name'],
                'unit_numbers': unit_numbers,
                'quantity': params.get('quantity', 1)
            }
            result['message'] = f"Adding {template['name']} to {len(unit_numbers)} units ({unit_numbers[0]} to {unit_numbers[-1]})"
            result['needs_refresh'] = True
        else:
            result['success'] = False
            result['message'] = f"Could not find template matching '{template_name}'"

    elif action == 'list_templates':
        if templates:
            template_list = "\n".join([f"- {t['name']}" for t in templates])
            result['message'] = f"Available templates:\n{template_list}"
        else:
            result['message'] = "No templates available yet. Create one from the Templates page."

    elif action == 'list_standards':
        if standard_cabinets:
            standard_list = "\n".join([
                f"- {s['name']} ({s.get('code', 'N/A')})"
                for s in standard_cabinets
            ])
            result['message'] = f"Available standard cabinets:\n{standard_list}"
        else:
            result['message'] = "No standard cabinets available yet. Create one from the Standards page."

    return result
