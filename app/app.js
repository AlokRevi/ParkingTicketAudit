// This file runs in the browser after index.html loads. The ArcPy script is separate.
// $ looks up an HTML element. esc turns record text into safe text for HTML templates.
const $ = (s) => document.querySelector(s),
  esc = (s) =>
    String(s ?? '').replace(
      /[&<>"']/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
    );

// Rule positions matter: validate() uses the same indexes when recording issues.
const rules = [
  [
    'Required fields',
    'Ticket number, issue date, issue time, location and fine must be present.',
  ],
  ['Missing values', 'Required values cannot be empty or contain whitespace only.'],
  ['Duplicate records', 'Repeated ticket numbers are flagged after their first occurrence.'],
  [
    'Data types',
    'Ticket numbers must contain digits and fines must be finite, non-negative numbers.',
  ],
  [
    'Date & time formats',
    'Dates must be valid YYYY-MM-DD or YYYYMMDD; times must be HHMM or HH:MM.',
  ],
];

// These variables hold the current dataset, selected rules, page number, and run results.
// They live in memory for the current browser session.
let enabled = rules.map(() => true),
  data = [],
  checked = false,
  page = 0,
  history = [],
  issues = [];

// Build fictional records so you can try the workflow without importing a file.
// Deliberately insert a few missing values, duplicates, and bad dates or times.
function sample() {
  return Array.from({ length: 15800 }, (_, i) => ({
    ticket_number: i && i % 157 === 0 ? String(100000 + i - 1) : String(100000 + i),
    date_of_infraction:
      i % 113 === 0 ? '2026-02-30' : `2026-10-${String((i % 28) + 1).padStart(2, '0')}`,
    time_of_infraction:
      i % 181 === 0 ? '2560' : String((i % 24) * 100 + (i % 60)).padStart(4, '0'),
    location:
      i % 97 === 0
        ? ''
        : ['QUEEN ST W', 'YONGE ST', 'BLOOR ST W', 'DUNDAS ST E', 'BAY ST', 'SPADINA AVE'][
            i % 6
          ],
    set_fine_amount: i % 239 === 0 ? 'unknown' : String([30, 50, 60, 100, 150][i % 5]),
  }));
}

// Convert compact dates to YYYY-MM-DD. Round-trip through Date to catch impossible
// calendar dates that JavaScript might otherwise normalize to a different day.
function dateValue(v) {
  let s = String(v ?? '').trim();
  if (/^\d{8}$/.test(s)) s = s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return null;
  let d = new Date(s + 'T00:00:00Z');
  return !isNaN(d) && d.toISOString().slice(0, 10) === s ? s : null;
}

// Pad short times such as 930 to 0930, then convert to 09:30.
// The final pattern accepts only hours 00–23 and minutes 00–59.
function timeValue(v) {
  let s = String(v ?? '').trim();
  if (/^\d{1,4}$/.test(s)) s = s.padStart(4, '0').replace(/(..)(..)/, '$1:$2');
  return /^([01]\d|2[0-3]):[0-5]\d$/.test(s) ? s : null;
}

// Check each row with the enabled rules, then save one summary for the processing log.
function validate() {
  let seen = new Set(),
    counts = rules.map(() => 0),
    stamp = new Date().toISOString();
  issues = [];
  data.forEach((r, i) => {
    // A record can fail several checks. Count each failed rule, but store one issue-log entry per row.
    let problems = [],
      add = (n, msg) => {
        if (enabled[n]) {
          problems.push(msg);
          counts[n]++;
        }
      };
    let fields = [
      'ticket_number',
      'date_of_infraction',
      'time_of_infraction',
      'location',
      'set_fine_amount',
    ];
    // An absent column is a schema issue. A present column with a blank cell is a missing value.
    if (fields.some((f) => !Object.hasOwn(r, f))) add(0, 'Required fields absent');
    if (fields.some((f) => Object.hasOwn(r, f) && !String(r[f] ?? '').trim()))
      add(1, 'Missing required value');
    // A Set remembers ticket numbers already seen. Only later occurrences are marked as repeats.
    let id = String(r.ticket_number ?? '').trim();
    if (id && seen.has(id)) add(2, 'Duplicate ticket number');
    if (id) seen.add(id);
    if (
      (id && !/^\d+$/.test(id)) ||
      (String(r.set_fine_amount ?? '').trim() &&
        (!Number.isFinite(Number(r.set_fine_amount)) || Number(r.set_fine_amount) < 0))
    )
      add(3, 'Invalid data type');
    let date = dateValue(r.date_of_infraction),
      time = timeValue(r.time_of_infraction);
    if ((r.date_of_infraction && !date) || (r.time_of_infraction && !time))
      add(4, 'Invalid date or time');
    // Keep original input fields and attach separate audit and standardized fields.
    r.audit_id = 'TOR-' + String(i + 1).padStart(6, '0');
    r.validation_status = problems.length ? 'review' : 'valid';
    r.validation_issues = problems.join('; ');
    r.processed_at = stamp;
    r.standardized_date = date || '';
    r.standardized_time = time || '';
    if (problems.length)
      issues.push({
        audit_id: r.audit_id,
        ticket_number: r.ticket_number,
        issues: r.validation_issues,
        processed_at: stamp,
      });
  });
  checked = true;
  history.unshift({
    stamp,
    total: data.length,
    invalid: issues.length,
    counts,
    // Copy the rule settings so later checkbox changes do not alter this run's history.
    enabled: [...enabled],
  });
  page = 0;
  render();
  toast('Validation complete. Audit fields and exports are ready.');
}

// Refresh the dashboard from the latest run. Before validation, display placeholders.
function render() {
  let n = data.length,
    bad = checked ? issues.length : 0,
    good = n - bad,
    pct = n ? ((good / n) * 100).toFixed(1) : '0.0';
  $('#stats').innerHTML = [
    ['TOTAL RECORDS', n.toLocaleString(), 'Current dataset'],
    [
      'VALID RECORDS',
      checked ? good.toLocaleString() : '—',
      checked ? 'Passed active checks' : 'Run validation to inspect',
    ],
    [
      'NEEDS REVIEW',
      checked ? bad.toLocaleString() : '—',
      checked ? 'Flagged for inspection' : 'Awaiting validation',
    ],
    ['ACTIVE CHECKS', enabled.filter(Boolean).length + '/5', 'Configurable validation rules'],
  ]
    .map(
      (x) =>
        `<div class="stat"><label>${x[0]}</label><strong>${x[1]}</strong><small>${x[2]}</small></div>`,
    )
    .join('');
  $('#score').textContent = checked ? pct + '%' : '—';
  $('#valid').textContent = checked ? good.toLocaleString() : '—';
  $('#invalid').textContent = checked ? bad.toLocaleString() : '—';
  $('#ring').style.background =
    `conic-gradient(var(--blue) 0% ${checked ? pct : 0}%, var(--ochre) ${checked ? pct : 0}% 100%)`;
  $('#runstamp').textContent = checked
    ? 'LATEST RUN · ' +
      new Date(history[0].stamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : 'READY TO VALIDATE';
  $('#breakdown').innerHTML = rules
    .map((r, i) => {
      let count = checked ? history[0].counts[i] : 0;
      return `<div class="barrow">${r[0]}<b>${checked ? count.toLocaleString() + ' issues' : 'Awaiting run'}</b><div class="bar"><i style="width:${checked ? Math.max(count ? 2 : 0, (count / Math.max(n, 1)) * 100) : 0}%"></i></div></div>`;
    })
    .join('');
  renderRows();
  $('#log').textContent = history.length
    ? history
        .map(
          (h) =>
            `${h.stamp}\n${h.total.toLocaleString()} records processed · ${(h.total - h.invalid).toLocaleString()} passed · ${h.invalid.toLocaleString()} flagged\nRules: ${rules
              .filter((_, i) => h.enabled[i])
              .map((x) => x[0])
              .join(', ')}\nAudit IDs: TOR-000001 onward · Original values preserved\n`,
        )
        .join('\n')
    : 'No runs yet. Run validation to create a processing audit.';
}

// Apply the search term and status filter together. Export uses the same filtered rows.
function filtered() {
  let term = $('#search').value.toLowerCase(),
    status = $('#filter').value;
  return data.filter(
    (r) =>
      (status === 'all' || r.validation_status === status) &&
      [r.ticket_number, r.location, r.audit_id].some((v) =>
        String(v ?? '')
          .toLowerCase()
          .includes(term),
      ),
  );
}

// Show eight records at a time. Keep the page index within range when the filter changes.
function renderRows() {
  let list = filtered(),
    max = Math.max(0, Math.ceil(list.length / 8) - 1);
  page = Math.min(page, max);
  $('#rows').innerHTML =
    list
      .slice(page * 8, page * 8 + 8)
      .map(
        (r) =>
          `<tr><td>${esc(r.audit_id || '—')}</td><td>${esc(r.ticket_number)}</td><td>${esc(r.standardized_date || r.date_of_infraction)}</td><td>${esc(r.standardized_time || r.time_of_infraction)}</td><td>${esc(r.location || '—')}</td><td>${Number.isFinite(Number(r.set_fine_amount)) && String(r.set_fine_amount).trim() ? '$' + Number(r.set_fine_amount).toFixed(2) : esc(r.set_fine_amount || '—')}</td><td><span class="tag ${!checked ? 'neutral' : r.validation_status === 'review' ? 'warn' : ''}">${!checked ? 'Unchecked' : r.validation_status === 'review' ? 'Needs review' : 'Valid'}</span>${r.validation_issues ? '<div style="font-size:9px;margin-top:6px">' + esc(r.validation_issues) + '</div>' : ''}</td></tr>`,
      )
      .join('') || '<tr><td colspan="7">No matching records.</td></tr>';
  $('#recordcount').textContent =
    `${list.length.toLocaleString()} records · ${checked ? 'Validated' : 'Not yet validated'}`;
  $('#pagination').textContent =
    `Showing ${list.length ? page * 8 + 1 : 0}–${Math.min(page * 8 + 8, list.length)} of ${list.length.toLocaleString()}`;
  $('#prev').disabled = page === 0;
  $('#next').disabled = page >= max;
}

// Show a temporary status message without interrupting the user's work.
function toast(msg) {
  $('#toast').textContent = msg;
  $('#toast').style.display = 'block';
  setTimeout(() => ($('#toast').style.display = 'none'), 4000);
}

// Read the CSV one character at a time. Commas and line breaks inside quotes
// belong to the cell. A doubled quote represents one literal quote.
function csvParse(text) {
  let rows = [],
    row = [],
    cell = '',
    quoted = false;
  for (let i = 0; i < text.length; i++) {
    let c = text[i];
    if (c === '"') {
      if (quoted && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (!quoted && cell !== '') throw Error('Unexpected quote in CSV');
      else quoted = !quoted;
    } else if (!quoted && (c === ',' || c === '\n' || c === '\r')) {
      row.push(cell);
      cell = '';
      if (c !== ',') {
        if (c === '\r' && text[i + 1] === '\n') i++;
        if (row.some((v) => v.trim())) rows.push(row);
        row = [];
      }
    } else cell += c;
  }
  if (quoted) throw Error('Unclosed quote in CSV');
  row.push(cell);
  if (row.some((v) => v.trim())) rows.push(row);
  if (rows.length < 2) throw Error('CSV needs headers and at least one data row');
  // The first row names the fields. Normalize those names and check every row has the same width.
  let heads = rows.shift().map((s) =>
    s
      .replace(/^\uFEFF/, '')
      .trim()
      .toLowerCase(),
  );
  if (heads.some((h) => !h) || new Set(heads).size !== heads.length)
    throw Error('Column names must be unique and nonempty');
  return rows.map((row, i) => {
    if (row.length !== heads.length)
      throw Error(`Row ${i + 2} has ${row.length} values; expected ${heads.length}`);
    return Object.fromEntries(heads.map((h, j) => [h, row[j]]));
  });
}

// Build a CSV and download it as a browser-created Blob. Escape quotes and add a
// leading apostrophe to values that begin with common spreadsheet formula characters.
function download(rows, name) {
  if (!rows.length) return toast('No records to export.');
  let keys = [...new Set(rows.flatMap(Object.keys))],
    quote = (v) =>
      '"' +
      String(v ?? '')
        .replace(/^[=+@-]/, "'$&")
        .replace(/"/g, '""') +
      '"',
    csv = [keys, ...rows.map((r) => keys.map((k) => r[k]))]
      .map((r) => r.map(quote).join(','))
      .join('\r\n');
  let u = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })),
    a = document.createElement('a');
  a.href = u;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(u), 1000);
}

// Connect the controls in index.html to their actions.
$('#run').onclick = () => {
  if (!enabled.some(Boolean)) return toast('Enable at least one validation rule.');
  validate();
};
$('#import').onclick = () => $('#file').click();

// Read and parse the new file before replacing the current dataset.
// Reset validation results because they apply to the previous dataset.
$('#file').onchange = async (e) => {
  let f = e.target.files[0];
  if (!f) return;
  try {
    let next = csvParse(await f.text());
    data = next;
    checked = false;
    issues = [];
    page = 0;
    $('#dataset').textContent = f.name;
    $('#source').textContent =
      `${data.length.toLocaleString()} imported records · Browser validation · No geodatabase connected`;
    render();
    toast('CSV imported. Ready for validation.');
  } catch (err) {
    toast(err.message);
  }
  e.target.value = '';
};
$('#search').oninput = $('#filter').onchange = () => {
  page = 0;
  renderRows();
};
$('#prev').onclick = () => {
  page--;
  renderRows();
};
$('#next').onclick = () => {
  page++;
  renderRows();
};
$('#export').onclick = () => {
  if (!checked) return toast('Run validation before exporting.');
  download(filtered(), 'parking-tickets-audit.csv');
};
$('#logexport').onclick = () => {
  if (!checked) return toast('Run validation first.');
  if (!issues.length)
    return download(
      [{ result: 'No validation issues', processed_at: history[0].stamp }],
      'validation-issues.csv',
    );
  download(issues, 'validation-issues.csv');
};

// Build the rule checkboxes from the same list used by validation.
$('#rulelist').innerHTML =
  rules
    .map(
      (r, i) =>
        `<label class="rule"><input type="checkbox" checked data-rule="${i}"><div><strong>${r[0]}</strong><p>${r[1]}</p></div></label>`,
    )
    .join('') +
  '<div class="rule"><div><strong>Run the ArcPy workflow</strong><p>Download the companion script for ArcGIS Pro. It inspects a feature class or table, creates a separate audited output in your file geodatabase, and writes a CSV issue log.</p><a href="arcpy_audit.py" download class="secondary">↓ Download ArcPy script</a></div></div>';
document.querySelectorAll('[data-rule]').forEach(
  (el) =>
    (el.onchange = () => {
      enabled[+el.dataset.rule] = el.checked;
      toast('Rule updated. Run validation to apply changes.');
    }),
);

// Switch views by showing or hiding sections. The data stays loaded.
document.querySelectorAll('nav button').forEach(
  (b) =>
    (b.onclick = () => {
      document
        .querySelectorAll('nav button')
        .forEach((x) => x.classList.toggle('active', x === b));
      let p = b.dataset.page;
      ['overview', 'records', 'rules', 'runs'].forEach((id) =>
        $('#' + id).classList.toggle(
          'hidden',
          p === 'overview' ? !['overview', 'records'].includes(id) : id !== p,
        ),
      );
      $('#title').textContent = {
        overview: 'A clearer view of your data.',
        records: 'Explore every record.',
        rules: 'Define quality, consistently.',
        runs: 'A traceable processing history.',
      }[p];
    }),
);

// Start with the synthetic dataset and show the first page.
data = sample();
render();
