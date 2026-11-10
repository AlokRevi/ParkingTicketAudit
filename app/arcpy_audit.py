"""ArcGIS Pro Python: python arcpy_audit.py input_table output_gdb output_name issue_log.csv

Required source fields: ticket_number, date_of_infraction, time_of_infraction,
location, set_fine_amount. Input is preserved; existing outputs are never replaced.
Dates: YYYYMMDD or YYYY-MM-DD (or ArcGIS date values). Times: HHMM or HH:MM.
Flagged rows remain in the audited copy for review. Filter AUDIT_STATUS = 'valid'
to select analysis-ready rows. AUDIT_NOTE records repeated masked IDs. For tables,
a repeat is flagged only when all comparable source fields match. Run with ArcGIS
Pro's licensed Python.
"""
import argparse
import csv
import datetime as dt
import math
import os
import re
import uuid

import arcpy


REQUIRED_FIELDS = [
    'ticket_number', 'date_of_infraction', 'time_of_infraction',
    'location', 'set_fine_amount',
]

# Each entry gives ArcGIS the field name, type, and text length.
AUDIT_FIELDS = [
    ('AUDIT_ID', 'TEXT', 36),
    ('AUDIT_STATUS', 'TEXT', 10),
    ('AUDIT_ISSUES', 'TEXT', 1000),
    ('STD_DATE', 'TEXT', 10),
    ('STD_TIME', 'TEXT', 5),
    ('PROCESSED_AT', 'DATE', None),
    ('AUDIT_NOTE', 'TEXT', 100),
]


def source_field_names(description):
    """Check required columns and return their original spellings."""
    fields = {field.name.lower(): field.name for field in description.fields}
    missing = set(REQUIRED_FIELDS) - fields.keys()
    if missing:
        raise ValueError('Missing source fields: ' + ', '.join(sorted(missing)))
    return fields


def output_path(args):
    """Check the destinations before creating any output."""
    if not args.output_gdb.lower().endswith('.gdb') or not arcpy.Exists(args.output_gdb):
        raise ValueError('Output must be an existing file geodatabase')
    if arcpy.ValidateTableName(args.output_name, args.output_gdb) != args.output_name:
        raise ValueError('Invalid output table name')
    output = os.path.join(args.output_gdb, args.output_name)
    if arcpy.Exists(output) or os.path.exists(args.issue_log):
        raise ValueError('Output or log already exists; choose new names')
    return output


def prepare_output(input_table, output, description, fields):
    """Copy the source and return the fields available for duplicate checks."""
    if any(name.lower() in fields for name, _, _ in AUDIT_FIELDS):
        raise ValueError('Source already contains reserved audit fields')

    arcpy.env.overwriteOutput = False
    if hasattr(description, 'shapeType'):
        arcpy.management.CopyFeatures(input_table, output)
    else:
        arcpy.management.CopyRows(input_table, output)

    # System IDs can differ even for identical source records. Geometry and complex
    # values disable exact row matching because this script cannot compare them.
    source_fields = [field for field in arcpy.ListFields(output)
                     if field.type not in {'OID', 'GlobalID'}]
    can_compare_rows = all(field.type not in {'Geometry', 'Blob', 'Raster'}
                           for field in source_fields)
    comparison_columns = [
        field.name for field in source_fields if field.name.lower() not in REQUIRED_FIELDS
    ] if can_compare_rows else []

    for name, kind, length in AUDIT_FIELDS:
        arcpy.management.AddField(output, name, kind, field_length=length)
    return can_compare_rows, comparison_columns


def normalize_date(value):
    """Return a YYYY-MM-DD string, or an empty string for an invalid date."""
    if isinstance(value, (dt.datetime, dt.date)):
        return value.strftime('%Y-%m-%d')
    raw = str(value).strip()
    date_format = '%Y%m%d' if len(raw) == 8 and raw.isdigit() else '%Y-%m-%d'
    try:
        parsed = dt.datetime.strptime(raw, date_format)
    except (ValueError, TypeError):
        return ''
    # Parsing alone can accept a date with missing leading zeros.
    return parsed.strftime('%Y-%m-%d') if parsed.strftime(date_format) == raw else ''


def normalize_time(value):
    """Return HH:MM, restoring leading zeros dropped during CSV import."""
    raw = str(value).strip()
    if raw.isdigit() and len(raw) <= 4:
        raw = raw.zfill(4)
        raw = raw[:2] + ':' + raw[2:]
    try:
        parsed = dt.datetime.strptime(raw, '%H:%M')
    except (ValueError, TypeError):
        return ''
    return raw if parsed.strftime('%H:%M') == raw else ''


def valid_fine(value):
    """Fines must be numeric, finite, and nonnegative."""
    try:
        amount = float(value)
    except (ValueError, TypeError):
        return False
    return math.isfinite(amount) and amount >= 0


def check_masked_ticket(key, signature, seen_ids, seen_masked_rows):
    """Distinguish a repeated masked ID from a matching source row."""
    note = 'Repeated masked ticket ID' if key in seen_ids else ''
    if signature is None:
        return [], note
    repeated_row = signature in seen_masked_rows
    seen_masked_rows.add(signature)
    problems = ['Matching source fields with repeated masked ticket ID'] if repeated_row else []
    return problems, note


def check_ticket_number(ticket, signature, seen_ids, seen_masked_rows):
    """Check ID format and repeats while keeping the source ID intact."""
    key = str(ticket or '').strip()
    if not key:
        return [], ''

    if re.fullmatch(r'\*{3}[0-9]{5}', key):
        # Masking can give different tickets the same ID.
        problems, note = check_masked_ticket(key, signature, seen_ids, seen_masked_rows)
    else:
        problems = []
        note = ''
        if key in seen_ids:
            problems.append('Duplicate ticket number')
        if not key.isdigit():
            problems.append('Invalid ticket ID format')

    seen_ids.add(key)
    return problems, note


def validate_record(required_values, signature, seen_ids, seen_masked_rows):
    """Collect review reasons and standardized values for one record."""
    ticket, date, time, _, fine = required_values
    problems = []
    if any(value is None or not str(value).strip() for value in required_values):
        problems.append('Missing required value')
    id_problems, note = check_ticket_number(ticket, signature, seen_ids, seen_masked_rows)
    problems.extend(id_problems)

    if not valid_fine(fine):
        problems.append('Invalid fine amount')
    std_date = normalize_date(date)
    if not std_date:
        problems.append('Invalid date')
    std_time = normalize_time(time)
    if not std_time:
        problems.append('Invalid time')
    return problems, note, std_date, std_time


def audit_rows(cursor, log, now, audit_start, audit_end, can_compare_rows):
    """Update the copied rows and log the ones that need review."""
    seen_ids, seen_masked_rows = set(), set()
    total = flagged = noted = 0
    for row in cursor:
        total += 1
        required_values = row[:audit_start]
        # Omit the newly added audit fields when comparing source values.
        signature = tuple(required_values) + tuple(row[audit_end:]) if can_compare_rows else None
        problems, note, std_date, std_time = validate_record(
            required_values, signature, seen_ids, seen_masked_rows,
        )

        audit_id = str(uuid.uuid4())
        status = 'review' if problems else 'valid'
        issue_text = '; '.join(problems)
        row[audit_start:audit_end] = [audit_id, status, issue_text, std_date, std_time, now, note]
        cursor.updateRow(row)

        if note:
            noted += 1
        if problems:
            flagged += 1
            log.writerow([audit_id, required_values[0], issue_text, now.isoformat() + 'Z'])
    return total, flagged, noted


def run_audit(args):
    """Set up the geodatabase copy, audit it, and print the run totals."""
    description = arcpy.Describe(args.input_table)
    fields = source_field_names(description)
    output = output_path(args)
    can_compare_rows, comparison_columns = prepare_output(
        args.input_table, output, description, fields,
    )

    # Cursor columns are ordered as required source fields, audit fields, then
    # any other source fields used for duplicate comparison.
    required_columns = [fields[name] for name in REQUIRED_FIELDS]
    audit_start = len(required_columns)
    audit_end = audit_start + len(AUDIT_FIELDS)
    columns = required_columns + [name for name, _, _ in AUDIT_FIELDS] + comparison_columns
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    # Opening with 'x' prevents replacing an existing issue log.
    with open(args.issue_log, 'x', newline='', encoding='utf-8') as stream:
        log = csv.writer(stream)
        log.writerow(['audit_id', 'ticket_number', 'issues', 'processed_at_utc'])
        with arcpy.da.UpdateCursor(output, columns) as cursor:
            total, flagged, noted = audit_rows(
                cursor, log, now, audit_start, audit_end, can_compare_rows,
            )
    print(f'Output: {output}; processed: {total}; valid: {total-flagged}; '
          f'flagged: {flagged}; repeated masked ID notes: {noted}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('input_table', 'output_gdb', 'output_name', 'issue_log'):
        parser.add_argument(name)
    run_audit(parser.parse_args())


if __name__ == '__main__':
    main()
