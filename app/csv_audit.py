"""Audit a parking-ticket CSV using only the Python standard library."""

import argparse
import csv
import datetime as dt
from itertools import islice
import math
from pathlib import Path
import re
import sys
import uuid


# The City uses different names for the ticket ID and street address.
# The first name in each group is the one used by the prepared CSV.
FIELD_NAMES = (
    ('ticket_number', 'tag_number_masked'),
    ('date_of_infraction',),
    ('time_of_infraction',),
    ('location', 'location2'),
    ('set_fine_amount',),
)

AUDIT_FIELDS = (
    'AUDIT_ID', 'AUDIT_STATUS', 'AUDIT_ISSUES', 'STD_DATE',
    'STD_TIME', 'PROCESSED_AT', 'AUDIT_NOTE',
)


def find_required_columns(header):
    """Return the indexes for ID, date, time, location, and fine."""
    names = [name.strip().lower() for name in header]
    if '' in names or len(names) != len(set(names)):
        raise ValueError('Column names must be unique and nonempty.')
    reserved = set(names) & {name.lower() for name in AUDIT_FIELDS}
    if reserved:
        raise ValueError('Source already contains audit columns: ' + ', '.join(sorted(reserved)))

    indexes = []
    for aliases in FIELD_NAMES:
        index = next((names.index(alias) for alias in aliases if alias in names), None)
        if index is None:
            raise ValueError('Missing required column: ' + ' or '.join(aliases))
        indexes.append(index)
    return indexes


def normalize_date(value):
    """Return YYYY-MM-DD for a real calendar date, or an empty string."""
    raw = value.strip()
    date_format = '%Y%m%d' if len(raw) == 8 and raw.isdigit() else '%Y-%m-%d'
    try:
        parsed = dt.datetime.strptime(raw, date_format)
    except ValueError:
        return ''
    return parsed.strftime('%Y-%m-%d') if parsed.strftime(date_format) == raw else ''


def normalize_time(value):
    """Return HH:MM, restoring leading zeros on compact times such as 930."""
    raw = value.strip()
    if re.fullmatch(r'[0-9]{1,4}', raw):
        raw = raw.zfill(4)
        raw = raw[:2] + ':' + raw[2:]
    return raw if re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', raw) else ''


def valid_fine(value):
    try:
        amount = float(value)
    except ValueError:
        return False
    return math.isfinite(amount) and amount >= 0


def check_ticket_id(ticket, row, seen_ids, seen_masked_rows):
    """Separate exact source-row matches from masked IDs that happen to repeat."""
    key = ticket.strip()
    if not key:
        return [], ''

    seen_before = key in seen_ids
    seen_ids.add(key)
    if re.fullmatch(r'\*{3}[0-9]{5}', key):
        note = 'Repeated masked ticket ID' if seen_before else ''
        signature = tuple(row)
        repeated_row = signature in seen_masked_rows
        seen_masked_rows.add(signature)
        problems = ['Matching source fields with repeated masked ticket ID'] if repeated_row else []
        return problems, note

    problems = []
    if seen_before:
        problems.append('Duplicate ticket number')
    if not key.isdigit():
        problems.append('Invalid ticket ID format')
    return problems, ''


def audit_row(row, indexes, seen_ids, seen_masked_rows):
    """Return the review reasons, note, and normalized values for one row."""
    values = [row[index] for index in indexes]
    ticket, date, time, _, fine = values
    problems = []
    if any(not value.strip() for value in values):
        problems.append('Missing required value')
    id_problems, note = check_ticket_id(ticket, row, seen_ids, seen_masked_rows)
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


def write_audit(rows, header, indexes, audited, log):
    """Keep every source row and write only records needing review to the log."""
    audited.writerow([*header, *AUDIT_FIELDS])
    log.writerow(['audit_id', 'ticket_number', 'issues', 'processed_at_utc'])
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    seen_ids, seen_masked_rows = set(), set()
    total = flagged = noted = 0

    for line_number, row in rows:
        if len(row) != len(header):
            raise ValueError(f'CSV row {line_number} has {len(row)} values, expected {len(header)}.')
        problems, note, std_date, std_time = audit_row(row, indexes, seen_ids, seen_masked_rows)
        audit_id = str(uuid.uuid4())
        status = 'review' if problems else 'valid'
        issue_text = '; '.join(problems)
        audited.writerow([*row, audit_id, status, issue_text, std_date, std_time, timestamp, note])
        if problems:
            log.writerow([audit_id, row[indexes[0]], issue_text, timestamp])
        total += 1
        flagged += bool(problems)
        noted += bool(note)

    return total, flagged, noted


def run_audit(source, audited_path, log_path, limit):
    """Open the outputs exclusively and remove any incomplete files on failure."""
    created = []
    try:
        with source.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.reader(stream, strict=True)
            header = next(reader, None)
            if not header:
                raise ValueError('The CSV must start with a header row.')
            indexes = find_required_columns(header)
            # Skip empty lines. islice lets us audit a sample without reading the full CSV.
            rows = ((number, row) for number, row in enumerate(reader, start=2) if row)
            with audited_path.open('x', encoding='utf-8', newline='') as audited:
                created.append(audited_path)
                with log_path.open('x', encoding='utf-8', newline='') as log:
                    created.append(log_path)
                    return write_audit(islice(rows, limit), header, indexes,
                                       csv.writer(audited), csv.writer(log))
    except (OSError, ValueError, csv.Error, KeyboardInterrupt):
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise


def default_paths():
    """Choose the project sample and a fresh output pair for a bare Run click."""
    # Resolve paths from this script so PyCharm's working directory does not matter.
    project = Path(__file__).resolve().parents[1]
    source = project / 'data' / 'prepared' / 'tickets.csv'
    exports = project / 'exports'
    run_id = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    return [
        str(source),
        str(exports / f'tickets_audited_{run_id}.csv'),
        str(exports / f'csv_validation_issues_{run_id}.csv'),
    ]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog='With no arguments, audit the project sample and write timestamped exports.',
    )
    parser.add_argument('input_csv', type=Path, help='City download or prepared CSV')
    parser.add_argument('audited_csv', type=Path, help='new CSV with all records and audit fields')
    parser.add_argument('issue_log', type=Path, help='new CSV listing records that need review')
    parser.add_argument('--limit', type=int, help='audit only the first N records')
    use_project_sample = not sys.argv[1:]
    args = parser.parse_args(default_paths() if use_project_sample else None)
    if args.limit is not None and args.limit <= 0:
        parser.error('--limit must be a positive number')

    try:
        if use_project_sample:
            if not args.input_csv.is_file():
                raise FileNotFoundError(
                    'Put a CSV at data/prepared/tickets.csv or pass the three file paths.'
                )
            args.audited_csv.parent.mkdir(exist_ok=True)
        total, flagged, noted = run_audit(args.input_csv, args.audited_csv, args.issue_log, args.limit)
    except (OSError, ValueError, csv.Error) as error:
        parser.exit(1, f'Audit failed: {error}\n')

    print(f'Processed: {total}; valid: {total - flagged}; flagged: {flagged}; '
          f'repeated masked ID notes: {noted}')
    print(f'Audited records: {args.audited_csv}\nIssue log: {args.issue_log}')


if __name__ == '__main__':
    main()
