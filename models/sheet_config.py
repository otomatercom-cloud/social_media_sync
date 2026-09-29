import csv
import hashlib
import io
import json
import logging
import re
import time
from datetime import timedelta

import requests
from urllib.parse import quote

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

SCI_RE = re.compile(r'^\d(\.\d+)?[eE]\+\d+$')
NO_UPDATE_FIELDS = {'phone_number', 'ad_id_raw', '_timestamp'}
DIRECT_FIELDS = (
    'email_address', 'college_name', 'last_studied_course', 'title', 'call_response',
    'academic_year', 'lead_quality', 'incoming_source', 'country', 'course_interested',
)
_TOKEN_CACHE = {}          # service-account e-mail -> (token, expiry)


def _h(text, n=12):
    return hashlib.md5((text or '').encode('utf-8')).hexdigest()[:n]


def _b64url(data):
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _google_token(sa_info):
    """Service-account access token (cached in memory for ~50 min)."""
    email = sa_info['client_email']
    cached = _TOKEN_CACHE.get(email)
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        raise UserError(_("The 'cryptography' Python package is required for Service Account access."))
    now = int(time.time())
    header = _b64url(json.dumps({'alg': 'RS256', 'typ': 'JWT'}).encode())
    payload = _b64url(json.dumps({
        'iss': email, 'scope': 'https://www.googleapis.com/auth/spreadsheets.readonly',
        'aud': 'https://oauth2.googleapis.com/token', 'iat': now, 'exp': now + 3600,
    }).encode())
    msg = ('%s.%s' % (header, payload)).encode()
    key = serialization.load_pem_private_key(sa_info['private_key'].encode(), password=None)
    sig = key.sign(msg, padding.PKCS1v15(), hashes.SHA256())
    r = requests.post('https://oauth2.googleapis.com/token', data={
        'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
        'assertion': '%s.%s.%s' % (header, payload, _b64url(sig))}, timeout=30)
    r.raise_for_status()
    token = r.json()['access_token']
    _TOKEN_CACHE[email] = (token, now + 3000)
    return token


class OtmSmSheetConfig(models.Model):
    _name = 'otm.sm.sheet.config'
    _description = 'Social Media Sync - Google Sheet Configuration'

    name = fields.Char(string='Config Name', required=True)
    active = fields.Boolean(default=True)

    # ── Connection ────────────────────────────────────────────────────────────
    auth_method = fields.Selection(
        [('public', 'Published / link-shared Sheet (no auth)'),
         ('service_account', 'Service Account JSON')],
        string='Authentication', default='public', required=True)
    sheet_url = fields.Char(string='Google Sheet URL')
    sheet_id = fields.Char(string='Sheet ID (auto)', compute='_compute_sheet_id', store=True)
    gid = fields.Char(string='Tab GID', default='0',
                      help='The gid= value in the sheet URL (used for public sheets). 0 = first tab.')
    tab_name = fields.Char(string='Tab Name', default='Sheet1',
                           help='Tab (worksheet) name - used for Service Account access.')
    service_account_json = fields.Text(string='Service Account JSON')
    fetch_mode = fields.Selection(
        [('range', 'Only new rows (fast, recommended)'),
         ('full', 'Download whole sheet, process only new rows')],
        string='Fetch Mode', default='range', required=True,
        help='"Only new rows" asks Google for just the rows after the last synced one, so a '
             'sheet with 100k rows costs the same as one with 10. Use "whole sheet" only if the '
             'range download fails for your sheet.')

    column_mapping_ids = fields.One2many('otm.sm.column.mapping', 'config_id', string='Column Mappings')

    # ── Sync position ─────────────────────────────────────────────────────────
    header_row = fields.Integer(string='Header Row Number', default=1,
                                help='Sheet row number that holds the column headers.')
    initial_row = fields.Integer(
        string='Start After Row', default=0,
        help='First sync only: ignore every row up to and including this sheet row number '
             '(use it to skip old history). 0 = start from the first data row.')
    last_row = fields.Integer(string='Last Synced Row', readonly=True, copy=False,
                              help='Sheet row number of the last row already synced. '
                                   'Rows up to here are never downloaded again.')
    recheck_rows = fields.Integer(
        string='Edit-Detection Window (rows)', default=50,
        help='The last N synced rows are re-read every run so a later edit in the sheet '
             '(e.g. officer name, course) updates ONLY the changed columns on the lead. '
             '0 = never re-read old rows.')
    batch_size = fields.Integer(string='Rows per Batch', default=500,
                                help='Max new rows read per request. Backlogs are processed batch by batch.')
    has_backlog = fields.Boolean(readonly=True, copy=False)
    header_json = fields.Text(readonly=True, copy=False)
    header_fetched_at = fields.Datetime(readonly=True, copy=False)

    # ── Lead defaults ─────────────────────────────────────────────────────────
    default_leads_source_id = fields.Many2one(
        'leads.sources', string='Default Lead Source',
        help='Used when a row has no Ad ID mapping.')
    default_lead_quality = fields.Selection(
        [('new', 'New'), ('first_attempt', 'First Attempt'), ('warm', 'Warm'), ('hot', 'Hot')],
        string='Default Lead Quality', default='new')

    # ── Schedule / status ─────────────────────────────────────────────────────
    sync_interval_minutes = fields.Integer(string='Check Every (minutes)', default=5)
    last_check_date = fields.Datetime(string='Last Checked', readonly=True, copy=False)
    last_sync_date = fields.Datetime(string='Last Time New Rows Were Found', readonly=True, copy=False)
    next_sync_date = fields.Datetime(string='Next Check At', compute='_compute_next_sync_date')

    sync_log_ids = fields.One2many('otm.sm.sync.log', 'config_id', string='Sync Logs (24h)')
    sync_log_count = fields.Integer(string='Logs (24h)', compute='_compute_counts')
    rows_tracked = fields.Integer(string='Rows Tracked', compute='_compute_counts')
    total_leads_created = fields.Integer(string='Leads Created', compute='_compute_counts')
    total_reattempts = fields.Integer(string='Re-Attempts', compute='_compute_counts')
    total_errors = fields.Integer(string='Error Rows', compute='_compute_counts')

    # ── Computes ──────────────────────────────────────────────────────────────
    @api.depends('sheet_url')
    def _compute_sheet_id(self):
        for rec in self:
            m = re.search(r'/spreadsheets/d/([a-zA-Z0-9_-]+)', rec.sheet_url or '')
            rec.sheet_id = m.group(1) if m else ''

    @api.depends('last_check_date', 'sync_interval_minutes')
    def _compute_next_sync_date(self):
        for rec in self:
            rec.next_sync_date = (rec.last_check_date + timedelta(minutes=max(rec.sync_interval_minutes or 5, 1))
                                  if rec.last_check_date else False)

    def _compute_counts(self):
        ids = [r.id for r in self if r.id]
        data = {}
        if ids:
            self.env.cr.execute(
                "SELECT config_id, result, COUNT(*) FROM otm_sm_sync_row "
                "WHERE config_id IN %s GROUP BY config_id, result", (tuple(ids),))
            for cid, res, cnt in self.env.cr.fetchall():
                data.setdefault(cid, {})[res] = cnt
            self.env.cr.execute(
                "SELECT config_id, COUNT(*) FROM otm_sm_sync_log WHERE config_id IN %s GROUP BY config_id",
                (tuple(ids),))
            logs = dict(self.env.cr.fetchall())
        else:
            logs = {}
        for rec in self:
            d = data.get(rec.id, {})
            rec.rows_tracked = sum(d.values())
            rec.total_leads_created = d.get('created', 0)
            rec.total_reattempts = d.get('re_enquiry', 0)
            rec.total_errors = d.get('error', 0)
            rec.sync_log_count = logs.get(rec.id, 0)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if not rec.column_mapping_ids:
                rec._create_default_mappings()
        return records

    def _create_default_mappings(self):
        defaults = [
            ('Name', 'name', True, 10), ('Phone', 'phone_number', True, 20),
            ('Email', 'email_address', False, 30), ('Ad Id', 'ad_id_raw', False, 40),
            ('Interested Course', 'course_interested', False, 50),
            ('Location', 'college_name', False, 60),
            ('Conversation Assigned to', 'lead_owner', False, 70),
            ('Date', '_timestamp', False, 80),
        ]
        self.env['otm.sm.column.mapping'].create([{
            'config_id': self.id, 'sheet_column': c, 'lead_field': f,
            'is_required': r, 'sequence': q} for c, f, r, q in defaults])

    # ── Buttons ───────────────────────────────────────────────────────────────
    def action_test_connection(self):
        self.ensure_one()
        return self.env['otm.sm.test.connection.wizard'].action_open(self.id)

    def action_sync_now(self):
        self.ensure_one()
        res = self._do_sync(triggered_by='manual', budget=40)
        msg = _('%(c)s leads created · %(r)s re-attempts · %(u)s updated · %(e)s errors.',
                c=res['created'], r=res['re'], u=res['updated'], e=res['err'])
        if res['backlog']:
            msg += ' ' + _('More rows are waiting - they continue automatically in the background.')
        if not res['rows']:
            msg = _('No new rows in the sheet.')
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Sync finished'), 'message': msg,
                           'type': 'success' if not res['err'] else 'warning', 'sticky': False}}

    def action_view_logs(self):
        self.ensure_one()
        return {'name': _('Sync Logs (last 24h)'), 'type': 'ir.actions.act_window',
                'res_model': 'otm.sm.sync.log', 'view_mode': 'list,form',
                'domain': [('config_id', '=', self.id)]}

    def action_view_error_rows(self):
        self.ensure_one()
        return {'name': _('Rows with errors'), 'type': 'ir.actions.act_window',
                'res_model': 'otm.sm.sync.row', 'view_mode': 'list',
                'domain': [('config_id', '=', self.id), ('result', '=', 'error')]}

    def action_purge_logs(self):
        self.env['otm.sm.sync.log']._purge_old(0)
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_retry_errors(self):
        """Rewind to the first errored row so it is re-evaluated (e.g. after adding an Ad ID mapping).
        Rows that were fine are skipped by their fingerprint - nothing is created twice."""
        self.ensure_one()
        self.env.cr.execute(
            "SELECT MIN(row_no) FROM otm_sm_sync_row WHERE config_id=%s AND result='error'", (self.id,))
        first = self.env.cr.fetchone()[0]
        if not first:
            raise UserError(_('There are no error rows to retry.'))
        self.write({'last_row': max(first - 1, self.header_row), 'has_backlog': True})
        return self.action_sync_now()

    def action_reset_tracking(self):
        """Forget everything: the next sync re-reads the sheet from the start."""
        self.ensure_one()
        self.env.cr.execute("DELETE FROM otm_sm_sync_row WHERE config_id=%s", (self.id,))
        self.write({'last_row': 0, 'has_backlog': False, 'last_sync_date': False})
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Tracking reset'), 'type': 'warning', 'sticky': False,
                           'message': _('Next sync starts again from the first data row.')}}

    @api.model
    def get_sync_interval(self):
        cfgs = self.search([('active', '=', True)])
        return (min(max(c.sync_interval_minutes or 5, 1) for c in cfgs) * 60) if cfgs else 300

    # ── Cron ──────────────────────────────────────────────────────────────────
    @api.model
    def _cron_sync_all(self):
        self.env['otm.sm.sync.log']._purge_old(24)          # 1-day log retention
        now = fields.Datetime.now()
        for cfg in self.search([('active', '=', True)]):
            gap = timedelta(minutes=max(cfg.sync_interval_minutes or 5, 1))
            if not cfg.has_backlog and cfg.last_check_date and now < cfg.last_check_date + gap:
                continue
            try:
                cfg._do_sync(triggered_by='cron', budget=50, raise_errors=False)
            except Exception:
                self.env.cr.rollback()
                _logger.exception('Social media sync failed for %s', cfg.name)
            self.env.cr.commit()

    # ── Google access (only ranges of rows, never the whole sheet) ───────────
    def _sa_info(self):
        if not self.service_account_json:
            raise UserError(_('Service Account JSON is required.'))
        try:
            return json.loads(self.service_account_json)
        except json.JSONDecodeError as e:
            raise UserError(_('Invalid service account JSON: %s') % e)

    def _fetch_cells(self, first, last, run=None):
        """Raw cell lists for sheet rows first..last (row i of the result = sheet row first+i)."""
        self.ensure_one()
        if not self.sheet_id:
            raise UserError(_('Could not read the Sheet ID from the URL.'))
        if self.auth_method == 'service_account':
            tab = "'%s'" % (self.tab_name or 'Sheet1').replace("'", "''")
            rng = quote('%s!A%d:ZZ%d' % (tab, first, last), safe='')
            url = ('https://sheets.googleapis.com/v4/spreadsheets/%s/values/%s'
                   '?valueRenderOption=FORMATTED_VALUE&majorDimension=ROWS' % (self.sheet_id, rng))
            resp = requests.get(url, headers={'Authorization': 'Bearer %s' % _google_token(self._sa_info())},
                                timeout=(10, 60))
            if resp.status_code == 400 and 'exceeds grid limits' in resp.text:
                return []
            resp.raise_for_status()
            return resp.json().get('values', [])
        # public / link-shared sheet
        if self.fetch_mode == 'full':
            if run is not None and run.get('full') is not None:
                rows = run['full']
            else:
                rows = self._download_csv(None)
                if run is not None:
                    run['full'] = rows
            return rows[first - 1:last]
        return self._download_csv('A%d:ZZ%d' % (first, last))

    def _download_csv(self, rng):
        url = 'https://docs.google.com/spreadsheets/d/%s/export?format=csv&gid=%s' % (self.sheet_id, self.gid or '0')
        if rng:
            url += '&range=%s' % rng
        resp = requests.get(url, timeout=(10, 90))
        if resp.status_code in (401, 403):
            raise UserError(_('Google refused access (%s). Share the sheet as "Anyone with the link - Viewer" '
                              'or use a Service Account.') % resp.status_code)
        if resp.status_code == 400:
            return []       # range starts below the last used row
        resp.raise_for_status()
        text = resp.content.decode('utf-8-sig')
        if text.lstrip()[:1] == '<':
            raise UserError(_('Google returned a web page instead of CSV. Check the sharing settings and the Tab GID, '
                              'or switch Fetch Mode to "Download whole sheet".'))
        return list(csv.reader(io.StringIO(text)))

    def _get_header(self, force=False):
        self.ensure_one()
        now = fields.Datetime.now()
        if (not force and self.header_json and self.header_fetched_at
                and now < self.header_fetched_at + timedelta(hours=1)):
            return json.loads(self.header_json)
        cells = self._fetch_cells(self.header_row, self.header_row)
        header = [str(c).strip() for c in (cells[0] if cells else [])]
        if not any(header):
            raise UserError(_('Header row %s is empty. Check "Header Row Number" and the Tab.') % self.header_row)
        self.write({'header_json': json.dumps(header), 'header_fetched_at': now})
        return header

    def _fetch_preview_rows(self, n=30):
        """Header + first n data rows as dicts (used by Test Connection)."""
        self.ensure_one()
        header = self._get_header(force=True)
        cells = self._fetch_cells(self.header_row + 1, self.header_row + n)
        return [self._row_dict(header, c) for c in cells if any(str(x).strip() for x in c)]

    @staticmethod
    def _row_dict(header, cells):
        d = {}
        for i, name in enumerate(header):
            if name and name not in d:
                d[name] = str(cells[i]).strip() if i < len(cells) and cells[i] is not None else ''
        return d

    # ── Core sync ─────────────────────────────────────────────────────────────
    def _do_sync(self, triggered_by='manual', budget=45, raise_errors=True):
        self.ensure_one()
        t0 = time.time()
        tot = dict(rows=0, created=0, re=0, updated=0, dup=0, err=0, backlog=False)
        errors = []
        run = {}
        try:
            header = self._get_header()
            cmap = self._mapping_info(header)
            ctx = self._build_ctx()
            while True:
                more = self._sync_batch(header, cmap, ctx, run, tot, errors)
                self.env.cr.commit()
                if not more:
                    break
                if time.time() - t0 > budget:
                    tot['backlog'] = True
                    break
        except Exception as e:
            self.env.cr.rollback()
            msg = str(e.args[0] if isinstance(e, UserError) and e.args else e)[:500]
            _logger.warning('Social media sync failed for %s: %s', self.name, msg)
            self._touch(False)
            self._log_failure(triggered_by, msg)
            self.env.cr.commit()
            if raise_errors:
                raise UserError(_('Sync failed: %s') % msg)
            return dict(tot, err=tot['err'] + 1)
        self._touch(tot['backlog'])
        if tot['rows'] or tot['err']:
            state = 'success'
            if tot['err']:
                state = 'partial' if (tot['created'] or tot['re'] or tot['updated']) else 'failed'
            self.env['otm.sm.sync.log'].create({
                'config_id': self.id, 'triggered_by': triggered_by, 'rows_fetched': tot['rows'],
                'leads_created': tot['created'], 'leads_skipped': tot['re'],
                'leads_updated': tot['updated'], 'leads_error': tot['err'], 'state': state,
                'error_details': ('First errors:\n' + '\n'.join(errors[:20])) if errors else False,
            })
            self.write({'last_sync_date': fields.Datetime.now()})
        self.env.cr.commit()
        return tot

    def _touch(self, backlog):
        self.env.cr.execute(
            "UPDATE otm_sm_sheet_config SET last_check_date = NOW() AT TIME ZONE 'UTC', has_backlog = %s WHERE id = %s",
            (bool(backlog), self.id))
        self.invalidate_recordset(['last_check_date', 'has_backlog', 'next_sync_date'])

    def _log_failure(self, triggered_by, msg):
        Log = self.env['otm.sm.sync.log']
        prev = Log.search([('config_id', '=', self.id), ('state', '=', 'failed'),
                           ('error_details', '=', msg),
                           ('sync_date', '>=', fields.Datetime.now() - timedelta(hours=1))], limit=1)
        if prev:                      # same error again: refresh instead of piling up logs
            prev.write({'sync_date': fields.Datetime.now()})
        else:
            Log.create({'config_id': self.id, 'triggered_by': triggered_by, 'state': 'failed',
                        'leads_error': 1, 'error_details': msg})

    def _mapping_info(self, header):
        cmap = []
        for cm in self.column_mapping_ids.sorted('sequence'):
            cmap.append({'field': cm.lead_field, 'col': cm.sheet_column.strip(),
                         'required': cm.is_required, 'default': cm.default_value or ''})
        fields_ = {c['field']: c for c in cmap}
        if 'phone_number' not in fields_:
            raise UserError(_('Map a sheet column to "Mobile / Phone" in the Column Mapping tab.'))
        missing = [c['col'] for c in cmap if c['col'] not in header]
        if fields_['phone_number']['col'] in missing:
            raise UserError(_('Phone column "%(c)s" not found in header row. Sheet headers: %(h)s',
                              c=fields_['phone_number']['col'], h=', '.join(h for h in header if h)))
        return {'list': cmap, 'by_field': fields_, 'missing': missing}

    def _build_ctx(self):
        ads = self.env['otm.sm.ad.mapping'].search_read(
            [('active', '=', True)], ['ad_id', 'source_campaign_id', 'leads_source_id'])
        return {
            'ads': {a['ad_id'].strip(): (a['source_campaign_id'][0] if a['source_campaign_id'] else 0,
                                         a['leads_source_id'][0] if a['leads_source_id'] else 0)
                    for a in ads},
            'emp': {}, 'sel': {},
        }

    # -- per-batch ---------------------------------------------------------------
    def _sync_batch(self, header, cmap, ctx, run, tot, errors):
        """Read one batch, process only new / changed rows. Returns True if more rows are waiting."""
        cr = self.env.cr
        h = self.header_row
        last = self.last_row or max(h, self.initial_row or 0)
        recheck = max(self.recheck_rows or 0, 0)
        batch = max(self.batch_size or 500, 50)
        start = max(h + 1, last - recheck + 1) if last > h else h + 1
        end = last + batch
        cells = self._fetch_cells(start, end, run)

        rows = []       # (row_no, cells, sig)
        new_region = 0
        max_row = last
        for i, c in enumerate(cells):
            if not any(str(x).strip() for x in c):
                continue
            row_no = start + i
            d = self._row_dict(header, c)
            sig = ''.join(_h(d.get(m['col'], ''), 4) for m in cmap['list'])
            rows.append((row_no, d, sig))
            if row_no > last:
                new_region += 1
                max_row = max(max_row, row_no)
        if not rows:
            return False

        cr.execute("SELECT row_no, col_sig, result, lead_id, key_hash FROM otm_sm_sync_row "
                   "WHERE config_id=%s AND row_no BETWEEN %s AND %s", (self.id, start, end))
        stored = {r[0]: r[1:] for r in cr.fetchall()}

        todo_new, todo_upd, refresh = [], [], []
        for row_no, d, sig in rows:
            st = stored.get(row_no)
            if not st:
                todo_new.append((row_no, d, sig))
            elif st[1] == 'error':
                todo_new.append((row_no, d, sig))          # retry (mapping may have been fixed)
            elif st[0] != sig:
                (todo_upd if st[1] == 'created' and st[2] else refresh).append((row_no, d, sig, st))

        out = []        # rows to upsert: (row_no, sig, key_hash, lead_id, result, error_msg)
        if todo_new:
            self._process_new(todo_new, stored, cmap, ctx, tot, errors, out)
        for row_no, d, sig, st in todo_upd:
            try:
                with cr.savepoint():
                    if self._update_lead(st[2], st[0], sig, d, cmap, ctx):
                        tot['updated'] += 1
                        tot['rows'] += 1
                out.append((row_no, sig, st[3], st[2], st[1], None))
            except Exception as e:
                tot['err'] += 1
                errors.append('Row %s update: %s' % (row_no, str(e)[:200]))
                out.append((row_no, st[0], st[3], st[2], st[1], None))   # keep old sig -> retried next run
        for row_no, d, sig, st in refresh:
            out.append((row_no, sig, st[3], st[2], st[1], None))

        if out:
            cr.executemany(
                """INSERT INTO otm_sm_sync_row (config_id, row_no, col_sig, key_hash, lead_id, result, error_msg)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (config_id, row_no) DO UPDATE SET col_sig = EXCLUDED.col_sig,
                       key_hash = EXCLUDED.key_hash, lead_id = EXCLUDED.lead_id,
                       result = EXCLUDED.result, error_msg = EXCLUDED.error_msg""",
                [(self.id,) + o for o in out])
        if max_row != self.last_row:
            self.write({'last_row': max_row})
        return new_region > 0 and len(cells) >= (end - start + 1)

    # -- new rows ------------------------------------------------------------------
    @staticmethod
    def _digits(v):
        return re.sub(r'\D', '', v or '')[-10:]

    def _cell(self, d, cmap, field):
        m = cmap['by_field'].get(field)
        if not m:
            return ''
        raw = (d.get(m['col'], '') or '').strip()
        if not raw:
            if m['required']:
                raise ValueError('Required column "%s" is empty' % m['col'])
            return m['default']
        return raw

    def _process_new(self, todo, stored, cmap, ctx, tot, errors, out):
        cr = self.env.cr
        Lead = self.env['leads.logic']
        phones, emails, keylist = set(), set(), []
        for _r, d, _s in todo:
            p = self._digits(self._safe_cell(d, cmap, 'phone_number'))
            if p:
                phones.add(p)
                keylist.append(self._row_key(d, cmap, ctx, p))
            e = self._safe_cell(d, cmap, 'email_address').lower()
            if e:
                emails.add(e)
        ex = {}
        if phones:
            cr.execute(
                "SELECT DISTINCT ON (p) p, id FROM (SELECT RIGHT(REPLACE(phone_number, ' ', ''), 10) AS p, id "
                "FROM leads_logic WHERE RIGHT(REPLACE(phone_number, ' ', ''), 10) = ANY(%s)) t ORDER BY p, id",
                (list(phones),))
            ex.update({('p', p): i for p, i in cr.fetchall()})
        if emails:
            cr.execute("SELECT DISTINCT ON (LOWER(email_address)) LOWER(email_address), id FROM leads_logic "
                       "WHERE LOWER(email_address) = ANY(%s) ORDER BY LOWER(email_address), id", (list(emails),))
            ex.update({('e', e): i for e, i in cr.fetchall()})
        keys = set()
        if keylist:
            cr.execute("SELECT key_hash FROM otm_sm_sync_row WHERE config_id=%s AND key_hash = ANY(%s) "
                       "AND result IN ('created','re_enquiry','duplicate')", (self.id, keylist))
            keys = {r[0] for r in cr.fetchall()}
        existing = ex

        for row_no, d, sig in todo:
            was_error = stored.get(row_no, (None, None))[1] == 'error'
            try:
                with cr.savepoint():
                    res, key, lead_id = self._process_row(d, cmap, ctx, existing, keys, Lead)
                keys.add(key)
                out.append((row_no, sig, key, lead_id, res, None))
                tot['rows'] += 1
                if res == 'created':
                    tot['created'] += 1
                elif res == 're_enquiry':
                    tot['re'] += 1
                else:
                    tot['dup'] += 1
            except Exception as e:
                msg = str(e.args[0] if e.args else e)[:160]
                out.append((row_no, sig, None, None, 'error', msg))
                if not was_error:              # do not count the same broken row again on every retry
                    tot['err'] += 1
                    tot['rows'] += 1
                    if len(errors) < 20:
                        errors.append('Row %s: %s' % (row_no, msg))

    def _row_key(self, d, cmap, ctx, p10):
        ad = self._safe_cell(d, cmap, 'ad_id_raw')
        sc_id = ctx['ads'].get(ad, (0, 0))[0] if ad else 0
        return _h('%s::%s' % (p10, sc_id))

    def _safe_cell(self, d, cmap, field):
        try:
            return self._cell(d, cmap, field)
        except ValueError:
            return ''

    def _process_row(self, d, cmap, ctx, existing, keys, Lead):
        phone_raw = self._cell(d, cmap, 'phone_number')
        phone = re.sub(r'\s+', '', phone_raw)
        p10 = self._digits(phone)
        if len(p10) < 6:
            raise ValueError('Phone "%s" is empty or invalid' % phone_raw)
        self._cell(d, cmap, 'name')                    # raises if required and empty

        # Ad ID -> Source Campaign / Lead Source
        ad = self._cell(d, cmap, 'ad_id_raw')
        sc_id = src_id = 0
        if ad:
            if SCI_RE.match(ad):
                raise ValueError('Ad ID "%s" is in scientific notation - format the Ad ID column as Plain text' % ad)
            if ad in ctx['ads']:
                sc_id, src_id = ctx['ads'][ad]
        if not src_id:
            if self.default_leads_source_id:
                src_id = self.default_leads_source_id.id
            else:
                raise ValueError('No Ad ID mapping for "%s" and no Default Lead Source' % ad)

        key = _h('%s::%s' % (p10, sc_id))
        if key in keys:
            return 'duplicate', key, None

        email = self._cell(d, cmap, 'email_address')
        lead_id = existing.get(('p', p10))
        dtype = 'phone'
        if not lead_id and email:
            lead_id = existing.get(('e', email.lower()))
            dtype = 'email'
        if lead_id:
            self._create_reattempt(Lead.browse(lead_id), d, cmap, ctx, phone, src_id, sc_id, dtype)
            return 're_enquiry', key, lead_id

        vals = {
            'name': self._cell(d, cmap, 'name') or 'Unknown',
            'phone_number': phone,
            'leads_source': src_id,
            'source_campaign_id': sc_id or False,
            'lead_quality': self.default_lead_quality or 'new',
        }
        owner = self._cell(d, cmap, 'lead_owner')
        if owner:
            emp = self._employee(ctx, owner)
            if emp:
                vals['lead_owner'] = emp
        vals.update(self._direct_vals(d, cmap, ctx, only=None))
        lead = Lead.create(vals)
        existing[('p', p10)] = lead.id
        if email:
            existing[('e', email.lower())] = lead.id
        return 'created', key, lead.id

    def _direct_vals(self, d, cmap, ctx, only=None):
        vals = {}
        for f in DIRECT_FIELDS:
            if f not in cmap['by_field'] or (only is not None and f not in only):
                continue
            v = self._cell(d, cmap, f)
            if not v:
                continue
            field = self.env['leads.logic']._fields.get(f)
            if not field:
                continue
            if field.type == 'selection':
                v = self._match_selection(ctx, f, v)
                if not v:
                    continue
            vals[f] = v
        return vals

    def _match_selection(self, ctx, fname, value):
        opts = ctx['sel'].get(fname)
        if opts is None:
            sel = self.env['leads.logic']._fields[fname]._description_selection(self.env)
            opts = {}
            for k, label in sel:
                opts[str(k).lower()] = k
                opts[str(label).lower()] = k
            ctx['sel'][fname] = opts
        return opts.get(value.strip().lower(), False)

    def _employee(self, ctx, name):
        cache = ctx['emp']
        if name not in cache:
            Emp = self.env['hr.employee']
            emp = Emp.search([('name', '=ilike', name)], limit=1) or Emp.search([('name', 'ilike', name)], limit=1)
            cache[name] = emp.id if emp else False
        return cache[name]

    def _create_reattempt(self, lead, d, cmap, ctx, phone, src_id, sc_id, dtype='phone'):
        parts = ['From Social Media Sync']
        if sc_id:
            parts.append('Campaign: %s' % self.env['lead.source.campaign'].browse(sc_id).name)
        for label, field in (('Location', 'college_name'), ('Channel', 'incoming_source')):
            v = self._safe_cell(d, cmap, field)
            if v:
                parts.append('%s: %s' % (label, v))
        vals = {
            'lead_id': lead.id,
            'existing_owner_id': lead.lead_owner.id if lead.lead_owner else False,
            'source_id': src_id or False,
            'remarks': '\n'.join(parts),
            'duplicate_type': dtype,
            'mobile': phone,
            'email': self._safe_cell(d, cmap, 'email_address') or False,
            'review_status': 'pending_review',
            're_attempt_count': (lead.re_attempt_count or 0) + 1,
        }
        course = self._safe_cell(d, cmap, 'course_interested')
        if course:
            c = self.env['course.interested'].search([('name', '=ilike', course)], limit=1)
            if c:
                vals['course_id'] = [(6, 0, c.ids)]
        self.env['otomater.lead.reattempt'].create(vals)

    # -- edits: only the changed columns ------------------------------------------------
    def _update_lead(self, lead_id, old_sig, new_sig, d, cmap, ctx):
        lead = self.env['leads.logic'].browse(lead_id).exists()
        if not lead:
            return False
        n = len(cmap['list'])
        if len(old_sig) != 4 * n:
            return False                       # mapping changed since - just refresh the fingerprint
        changed = {cmap['list'][i]['field'] for i in range(n)
                   if old_sig[4 * i:4 * i + 4] != new_sig[4 * i:4 * i + 4]} - NO_UPDATE_FIELDS
        vals = {}
        if 'name' in changed:
            v = self._safe_cell(d, cmap, 'name')
            if v:
                vals['name'] = v
        if 'lead_owner' in changed:
            v = self._safe_cell(d, cmap, 'lead_owner')
            emp = self._employee(ctx, v) if v else False
            if emp:
                vals['lead_owner'] = emp
        vals.update(self._direct_vals(d, cmap, ctx, only=changed))
        vals = {k: v for k, v in vals.items() if lead[k] != v and not (k == 'lead_owner' and lead.lead_owner.id == v)}
        if vals:
            lead.write(vals)
            return True
        return False
