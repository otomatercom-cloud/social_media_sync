import csv
import io
import json
import logging
import re

import requests

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class OtmSmTestConnectionWizard(models.TransientModel):
    """
    Opened by the 'Test Connection' button on the sheet config form.
    Fetches the first 5 rows and shows:
      - Detected sheet headers
      - Column-mapping status (matched / missing / unmapped)
      - Sample row preview
      - Ad ID mapping check
      - Officer name check
    """
    _name = 'otm.sm.test.connection.wizard'
    _description = 'Social Media Sheet — Test Connection & Preview'

    config_id = fields.Many2one('otm.sm.sheet.config', string='Config', readonly=True)

    # ── Result fields (populated by _run_test) ────────────────────────────
    state = fields.Selection(
        [('pending', 'Pending'), ('ok', 'OK'), ('error', 'Error')],
        default='pending',
    )
    sheet_headers_raw  = fields.Char(string='Detected Sheet Headers', readonly=True)
    rows_fetched       = fields.Integer(string='Sample Rows Read', readonly=True)
    mapping_status     = fields.Text(string='Column Mapping Check', readonly=True)
    ad_id_check        = fields.Text(string='Ad ID Check (first 5 rows)', readonly=True)
    officer_check      = fields.Text(string='Officer Name Check (first 5 rows)', readonly=True)
    sample_preview     = fields.Text(string='Sample Row Preview (first 3 rows)', readonly=True)
    error_message      = fields.Text(string='Error', readonly=True)

    @api.model
    def action_open(self, config_id):
        wizard = self.create({'config_id': config_id, 'state': 'pending'})
        wizard._run_test()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Test Connection Result',
            'res_model': self._name,
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _run_test(self):
        cfg = self.config_id
        lines = []
        ad_lines = []
        officer_lines = []

        # ── 1. Fetch sheet ────────────────────────────────────────────────
        try:
            rows = cfg._fetch_preview_rows(30)
        except Exception as e:
            self.state = 'error'
            self.error_message = (
                f"❌ Could not fetch the sheet.\n\n"
                f"Reason: {e}\n\n"
                f"Common fixes:\n"
                f"  • Make sure the sheet is published:\n"
                f"    File → Share → Publish to web → CSV\n"
                f"  • Check the Sheet URL is correct\n"
                f"  • Check the Tab GID matches the right tab"
            )
            return

        if not rows:
            self.state = 'error'
            self.error_message = (
                "Sheet returned 0 data rows.\n\n"
                "Common fixes:\n"
                "  • The sheet may be empty (no data below the header row)\n"
                "  • 'Skip Header Rows' might be set too high\n"
                "  • Check the Tab GID — it may point to the wrong tab"
            )
            return

        self.rows_fetched = len(rows)

        # ── 2. Detected headers ───────────────────────────────────────────
        try:
            headers = list(rows[0].keys())
            # Format as numbered list for easy reading
            self.sheet_headers_raw = '  |  '.join(headers)
        except Exception as e:
            self.state = 'error'
            self.error_message = f"Could not read sheet headers: {e}"
            return

        # ── 3. Column mapping check ───────────────────────────────────────
        try:
            lines.append('=== COLUMN MAPPING STATUS ===\n')
            mapped_sheet_cols = {cm.sheet_column: cm.lead_field for cm in cfg.column_mapping_ids}

            if not cfg.column_mapping_ids:
                lines.append('  ⚠️  No column mappings defined yet.')
                lines.append('  → Go to Column Mapping tab and add your sheet headers.')
            else:
                for cm in cfg.column_mapping_ids:
                    if cm.sheet_column in headers:
                        lines.append(f'  ✅  "{cm.sheet_column}"  →  {cm.lead_field_label}')
                    else:
                        close = [h for h in headers if cm.sheet_column.lower() in h.lower()
                                 or h.lower() in cm.sheet_column.lower()]
                        if close:
                            hint = f'\n       💡 Fix: change to "{close[0]}" in Column Mapping tab'
                        else:
                            hint = f'\n       Available headers: {chr(44).join(headers[:8])}'
                        lines.append(
                            f'  ❌  "{cm.sheet_column}"  →  {cm.lead_field_label}'
                            f'  — NOT FOUND in sheet{hint}'
                        )

            unmapped = [h for h in headers if h not in mapped_sheet_cols]
            if unmapped:
                lines.append('\n--- Sheet columns with no mapping (will be ignored) ---')
                for h in unmapped:
                    lines.append(f'  ⚪  "{h}"')

            self.mapping_status = '\n'.join(lines)
        except Exception as e:
            self.mapping_status = f'Error during mapping check: {e}'

        # ── 4. Sample preview ─────────────────────────────────────────────
        try:
            preview_lines = ['=== FIRST 3 ROWS ===\n']
            for i, row in enumerate(rows[:3], 1):
                preview_lines.append(f'Row {i}:')
                for k, v in row.items():
                    preview_lines.append(f'  {k!r}: {v!r}')
                preview_lines.append('')
            self.sample_preview = '\n'.join(preview_lines)
        except Exception as e:
            self.sample_preview = f'Error generating preview: {e}'

        # ── 5. Ad ID check ────────────────────────────────────────────────
        try:
            ad_col_meta = next(
                (cm for cm in cfg.column_mapping_ids if cm.lead_field == 'ad_id_raw'), None
            )
            if ad_col_meta:
                if ad_col_meta.sheet_column not in headers:
                    ad_lines.append(
                        f'❌ Ad ID column "{ad_col_meta.sheet_column}" not found in sheet.\n'
                        f'   Available headers: {", ".join(headers)}'
                    )
                else:
                    ad_map = {m.ad_id: m for m in self.env['otm.sm.ad.mapping'].search([])}
                    if not ad_map:
                        ad_lines.append(
                            '⚠️  No Ad ID Mappings configured yet.\n'
                            '   → Go to Social Media → Ad ID Mappings and add your ad IDs.'
                        )
                    else:
                        ad_lines.append(f'Ad ID column in sheet: "{ad_col_meta.sheet_column}"\n')
                        seen = set()
                        for row in rows[:30]:
                            ad_val = (row.get(ad_col_meta.sheet_column, '') or '').strip()
                            if not ad_val or ad_val in seen:
                                continue
                            seen.add(ad_val)
                            if ad_val in ad_map:
                                m = ad_map[ad_val]
                                ad_lines.append(
                                    f'  ✅  "{ad_val}"'
                                    f'  →  {m.source_campaign_id.name or "?"}'
                                    f'  (source: {m.leads_source_id.name or "?"})'
                                )
                            else:
                                ad_lines.append(
                                    f'  ❌  "{ad_val}"  →  NO MAPPING FOUND\n'
                                    f'       Add this Ad ID in Social Media → Ad ID Mappings'
                                )
            else:
                if cfg.default_leads_source_id:
                    ad_lines.append(
                        f'⚪  No Ad ID column mapped.\n'
                        f'   All leads will use default source: '
                        f'"{cfg.default_leads_source_id.name}"'
                    )
                else:
                    ad_lines.append(
                        '❌  No Ad ID column mapped AND no Default Lead Source set.\n'
                        '   → Either map the Ad ID column OR set a Default Lead Source\n'
                        '     in the Lead Defaults tab.'
                    )
            self.ad_id_check = '\n'.join(ad_lines)
        except Exception as e:
            self.ad_id_check = f'Error during Ad ID check: {e}'

        # ── 6. Officer check ──────────────────────────────────────────────
        try:
            off_col_meta = next(
                (cm for cm in cfg.column_mapping_ids if cm.lead_field == 'lead_owner'), None
            )
            if off_col_meta:
                if off_col_meta.sheet_column not in headers:
                    officer_lines.append(
                        f'❌ Officer column "{off_col_meta.sheet_column}" not found in sheet.\n'
                        f'   Available headers: {", ".join(headers)}'
                    )
                else:
                    officer_lines.append(f'Officer column: "{off_col_meta.sheet_column}"\n')
                    seen_off = set()
                    for row in rows[:30]:
                        off_val = (row.get(off_col_meta.sheet_column, '') or '').strip()
                        if not off_val or off_val in seen_off:
                            continue
                        seen_off.add(off_val)
                        emp = self.env['hr.employee'].search(
                            [('name', '=ilike', off_val)], limit=1
                        ) or self.env['hr.employee'].search(
                            [('name', 'ilike', off_val)], limit=1
                        )
                        if emp:
                            officer_lines.append(
                                f'  ✅  "{off_val}"  →  {emp.name}  (ID:{emp.id})'
                            )
                        else:
                            officer_lines.append(
                                f'  ❌  "{off_val}"  →  NOT FOUND in Employees\n'
                                f'       Lead will be created without officer assignment'
                            )
            else:
                officer_lines.append('⚪  No column mapped to Admission Officer.')
            self.officer_check = '\n'.join(officer_lines)
        except Exception as e:
            self.officer_check = f'Error during officer check: {e}'

        # ── 7. Final state ────────────────────────────────────────────────
        has_error = (
            '❌' in self.mapping_status
            or '❌' in self.ad_id_check
            or '❌' in self.officer_check
            or '⚠️' in self.ad_id_check
        )
        self.state = 'error' if has_error else 'ok'

