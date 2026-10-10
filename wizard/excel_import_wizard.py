import base64
import io
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AdExcelImportWizard(models.TransientModel):
    """
    Import Ad IDs from a Meta Ads Excel export.

    Expected columns (any order, header names are case-insensitive):
        Campaign name / Campaign                 →  otm.sm.meta.campaign
        Ad set name / Ad Set                     →  otm.sm.meta.adset
        Ad name / Source Campaign                →  lead.source.campaign (auto-created if missing)
        Ad ID                                    →  otm.sm.ad.mapping.ad_id
        Lead Source (auto) / Lead Source  (opt.) →  leads.sources by name (default source if empty)
        Active (opt.)                            →  False = archived mapping

    Lead Source is auto-filled from the Source Campaign's parent Lead Source.
    """
    _name = 'otm.sm.excel.import.wizard'
    _description = 'Ad ID Excel Import Wizard'

    # ── Step 1: Upload ─────────────────────────────────────────────────────
    state = fields.Selection(
        [('upload', 'Upload'), ('preview', 'Preview'), ('done', 'Done')],
        default='upload',
    )
    excel_file    = fields.Binary(string='Excel / CSV File', attachment=False)
    file_name     = fields.Char(string='File Name')
    sheet_name    = fields.Char(
        string='Sheet Name',
        default='Formatted Report',
        help='Sheet tab name. Leave blank to use the first sheet.',
    )

    # Default Lead Source applied to all auto-created Source Campaigns
    default_leads_source_id = fields.Many2one(
        'leads.sources', string='Default Lead Source',
        required=True,
        help='All Source Campaigns created from the Ad name column will be '
             'placed under this Lead Source.',
    )

    # ── Step 2: Preview counts ──────────────────────────────────────────────
    preview_text     = fields.Text(string='Preview', readonly=True)
    rows_total       = fields.Integer(string='Total Rows', readonly=True)
    campaigns_new    = fields.Integer(string='New Campaigns', readonly=True)
    adsets_new       = fields.Integer(string='New Ad Sets', readonly=True)
    source_camps_new = fields.Integer(string='New Source Campaigns', readonly=True)
    ads_new          = fields.Integer(string='New Ad IDs', readonly=True)
    ads_skip         = fields.Integer(string='Duplicate Ad IDs (skip)', readonly=True)

    # ── Step 3: Result ──────────────────────────────────────────────────────
    result_text = fields.Text(string='Import Result', readonly=True)

    # ── Parse helpers ───────────────────────────────────────────────────────
    def _parse_file(self):
        if not self.excel_file:
            raise UserError(_('Please upload a file.'))
        raw = base64.b64decode(self.excel_file)
        fname = (self.file_name or '').lower()

        if fname.endswith('.csv'):
            import csv, io as _io
            text = raw.decode('utf-8-sig')
            reader = csv.DictReader(_io.StringIO(text))
            return list(reader)

        # Excel
        try:
            import openpyxl
        except ImportError:
            raise UserError(_(
                "openpyxl is required. Install it: pip install openpyxl --break-system-packages"
            ))
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        if self.sheet_name and self.sheet_name in wb.sheetnames:
            ws = wb[self.sheet_name]
        else:
            ws = wb.active

        rows_raw = list(ws.iter_rows(values_only=True))
        if not rows_raw:
            return []
        headers = [str(h).strip() if h else '' for h in rows_raw[0]]
        result  = []
        for row in rows_raw[1:]:
            if not any(row):
                continue
            result.append({headers[i]: self._cell_text(row[i]) for i in range(len(headers))})
        return result

    @staticmethod
    def _cell_text(v):
        # long numeric IDs (Ad ID) must never turn into 1.2E+17
        if v is None:
            return ''
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v).strip()

    @staticmethod
    def _clean(v):
        """Collapse runs of spaces / tabs / non-breaking spaces; empty-safe."""
        return ' '.join(str(v or '').replace('\u00a0', ' ').split())

    @staticmethod
    def _key(v):
        return AdExcelImportWizard._clean(v).casefold()

    @staticmethod
    def _clean_ad_id(v):
        """'120250590667940593.' / '1.2E+17' safe: keep digits when the value is numeric-like."""
        v = AdExcelImportWizard._clean(v).replace(',', '')
        if v.endswith('.'):
            v = v[:-1]
        if v.endswith('.0'):
            v = v[:-2]
        return v

    _COLS = {
        'camp':   ('campaign name', 'campaign_name', 'campaign'),
        'adset':  ('ad set name', 'adset_name', 'ad set', 'adset'),
        'ad':     ('ad name', 'ad_name', 'source campaign', 'source_campaign'),
        'ad_id':  ('ad id', 'ad_id', 'adid'),
        'source': ('lead source (auto)', 'lead source', 'lead_source', 'source'),
        'active': ('active',),
    }

    def _norm_row(self, row):
        """One file row -> clean values. Header names are case/space-insensitive."""
        low = {self._key(k): v for k, v in row.items() if k}
        def pick(name):
            for k in self._COLS[name]:
                v = low.get(k)
                if v not in (None, ''):
                    return v
            return ''
        active_raw = self._key(pick('active'))
        return {
            'camp': self._clean(pick('camp')),
            'adset': self._clean(pick('adset')),
            'ad_name': self._clean(pick('ad')),
            'ad_id': self._clean_ad_id(pick('ad_id')),
            'source': self._clean(pick('source')),
            'active': active_raw not in ('false', '0', 'no', 'archived'),
        }

    def _source_for(self, name, cache, default):
        """Lead Source named in the file (e.g. META); falls back to the wizard default."""
        if not name:
            return default
        k = self._key(name)
        if k not in cache:
            cache[k] = self.env['leads.sources'].search([('name', '=ilike', name.strip())], limit=1)
        return cache[k] or default

    # ── Actions ─────────────────────────────────────────────────────────────
    def action_preview(self):
        self.ensure_one()
        if not self.default_leads_source_id:
            raise UserError(_('Please set a Default Lead Source before previewing.'))
        rows = self._parse_file()
        if not rows:
            raise UserError(_('File is empty or could not be parsed.'))

        existing_adids = set(self.env['otm.sm.ad.mapping'].with_context(active_test=False).search([]).mapped('ad_id'))
        camps = {self._key(c.name) for c in self.env['otm.sm.meta.campaign'].with_context(active_test=False).search([])}
        adsets = {(self._key(a.campaign_id.name), self._key(a.name))
                  for a in self.env['otm.sm.meta.adset'].with_context(active_test=False).search([])}
        srcs = {(c.lead_source_id.id, self._key(c.name))
                for c in self.env['lead.source.campaign'].with_context(active_test=False).search([])}
        src_cache = {}
        seen_ads, skip_ads = set(), set()
        new_camps, new_adsets, new_srcs, missing = set(), set(), set(), []
        samples = []
        for row in rows:
            r = self._norm_row(row)
            if not r['ad_id']:
                continue
            if r['ad_id'] in existing_adids or r['ad_id'] in seen_ads:
                skip_ads.add(r['ad_id'])
                continue
            if not (r['camp'] and r['adset'] and r['ad_name']):
                missing.append(r['ad_id'])
                continue
            seen_ads.add(r['ad_id'])
            src = self._source_for(r['source'], src_cache, self.default_leads_source_id)
            if self._key(r['camp']) not in camps:
                new_camps.add(self._key(r['camp']))
            if (self._key(r['camp']), self._key(r['adset'])) not in adsets:
                new_adsets.add((self._key(r['camp']), self._key(r['adset'])))
            if (src.id, self._key(r['ad_name'])) not in srcs:
                new_srcs.add((src.id, self._key(r['ad_name'])))
            if len(samples) < 5:
                samples.append(r)

        self.rows_total = len(rows)
        self.campaigns_new = len(new_camps)
        self.adsets_new = len(new_adsets)
        self.source_camps_new = len(new_srcs)
        self.ads_new = len(seen_ads)
        self.ads_skip = len(skip_ads)
        lines = [
            f'Total rows in file:          {len(rows)}',
            f'Ad IDs to import:            {len(seen_ads)}',
            f'Already imported (skip):     {len(skip_ads)}',
            f'Rows missing Campaign / Ad Set / Source Campaign: {len(missing)}',
            '',
            f'New Meta Campaigns:          {len(new_camps)}',
            f'New Meta Ad Sets:            {len(new_adsets)}',
            f'New Source Campaigns (Odoo): {len(new_srcs)}',
            '',
            'Names are matched ignoring upper/lower case and extra spaces.',
            'If the same name already exists several times, the oldest one is used.',
            '', '--- Sample (first 5 rows) ---',
        ]
        for r in samples:
            lines += [f"  [{r['ad_id']}]", f"    Campaign:        {r['camp']}", f"    Ad Set:          {r['adset']}",
                      f"    Source Campaign: {r['ad_name']}", f"    Lead Source:     {r['source'] or '(default)'}"]
        self.preview_text = '\n'.join(lines)
        self.state = 'preview'
        return self._reopen()

    def action_import(self):
        self.ensure_one()
        rows = self._parse_file()
        Camp, Adset = self.env['otm.sm.meta.campaign'], self.env['otm.sm.meta.adset']
        SrcCamp, AdMap = self.env['lead.source.campaign'], self.env['otm.sm.ad.mapping']
        default_src = self.default_leads_source_id

        existing_adids = set(AdMap.with_context(active_test=False).search([]).mapped('ad_id'))
        # oldest record wins when the database already holds duplicate names
        camp_cache, adset_cache, src_cache, srcamp_cache = {}, {}, {}, {}
        for c in Camp.with_context(active_test=False).search([], order='id desc'):
            camp_cache[self._key(c.name)] = c
        for a in Adset.with_context(active_test=False).search([], order='id desc'):
            adset_cache[(a.campaign_id.id, self._key(a.name))] = a
        for sc in SrcCamp.with_context(active_test=False).search([], order='id desc'):
            srcamp_cache[(sc.lead_source_id.id, self._key(sc.name))] = sc

        created = skipped = 0
        error_lines = []
        for row in rows:
            r = self._norm_row(row)
            if not r['ad_id']:
                continue
            if r['ad_id'] in existing_adids:
                skipped += 1
                continue
            if not (r['camp'] and r['adset'] and r['ad_name']):
                error_lines.append(f"❌ Ad ID {r['ad_id']}: Campaign, Ad Set and Source Campaign are all required.")
                continue
            try:
                with self.env.cr.savepoint():
                    src = self._source_for(r['source'], src_cache, default_src)
                    ck = self._key(r['camp'])
                    if ck not in camp_cache:
                        camp_cache[ck] = Camp.create({'name': r['camp']})
                    campaign = camp_cache[ck]
                    ak = (campaign.id, self._key(r['adset']))
                    if ak not in adset_cache:
                        adset_cache[ak] = Adset.create({'name': r['adset'], 'campaign_id': campaign.id})
                    adset = adset_cache[ak]
                    sk = (src.id, self._key(r['ad_name']))
                    if sk not in srcamp_cache:
                        srcamp_cache[sk] = SrcCamp.create({'name': r['ad_name'], 'lead_source_id': src.id})
                    AdMap.create({
                        'adset_id': adset.id, 'ad_id': r['ad_id'],
                        'source_campaign_id': srcamp_cache[sk].id, 'active': r['active'],
                    })
                    existing_adids.add(r['ad_id'])
                    created += 1
            except Exception as e:  # noqa: BLE001 - keep importing the other rows
                error_lines.append(f"❌ Ad ID {r['ad_id']}: {e}")
                _logger.error('Excel import error for ad_id %s: %s', r['ad_id'], e)

        result = [f'✅ Created:          {created}',
                  f'⚠️  Skipped (already imported): {skipped}',
                  f'❌ Errors:           {len(error_lines)}']
        if error_lines:
            result += ['', '--- Errors ---'] + error_lines[:50]
        self.result_text = '\n'.join(result)
        self.state = 'done'
        return self._reopen()

    def action_back(self):
        self.state = 'upload'
        return self._reopen()

    def action_close(self):
        return {'type': 'ir.actions.act_window_close'}

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
