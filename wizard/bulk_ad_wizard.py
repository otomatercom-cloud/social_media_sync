from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class BulkAdWizardLine(models.TransientModel):
    """One row in the bulk-add table."""
    _name = 'otm.sm.bulk.ad.wizard.line'
    _description = 'Bulk Ad Wizard Line'

    wizard_id       = fields.Many2one('otm.sm.bulk.ad.wizard', ondelete='cascade')
    campaign_id     = fields.Many2one(
        'otm.sm.meta.campaign', string='Campaign', required=True,
    )
    adset_id        = fields.Many2one(
        'otm.sm.meta.adset', string='Ad Set', required=True,
        domain="[('campaign_id','=',campaign_id)]",
    )
    ad_id           = fields.Char(string='Ad ID', required=True)
    source_campaign_id = fields.Many2one(
        'lead.source.campaign', string='Source Campaign', required=True,
    )
    leads_source_id = fields.Many2one(
        'leads.sources', string='Lead Source (auto)',
        related='source_campaign_id.lead_source_id',
        readonly=True, store=False,
    )
    status          = fields.Char(string='Status', readonly=True)

    @api.onchange('campaign_id')
    def _onchange_campaign(self):
        self.adset_id = False


class BulkAdWizard(models.TransientModel):
    """
    Wizard to bulk-create Ad ID mappings.
    User fills a table: Campaign → Ad Set → Ad ID → Source Campaign
    """
    _name = 'otm.sm.bulk.ad.wizard'
    _description = 'Bulk Add Ad ID Mappings'

    line_ids = fields.One2many(
        'otm.sm.bulk.ad.wizard.line', 'wizard_id', string='Ad Mappings',
    )
    result_summary = fields.Text(string='Result', readonly=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('done', 'Done')],
        default='draft',
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        # Seed 5 empty lines for convenience
        if 'line_ids' in fields_list:
            res['line_ids'] = [(0, 0, {}) for _ in range(5)]
        return res

    def action_add_lines(self):
        """Add 5 more empty rows."""
        self.write({
            'line_ids': [(0, 0, {}) for _ in range(5)],
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_create(self):
        """Create all valid lines as otm.sm.ad.mapping records."""
        AdMap   = self.env['otm.sm.ad.mapping']
        created = skipped = errors = 0
        lines   = self.line_ids.filtered(
            lambda l: l.adset_id and l.ad_id and l.ad_id.strip() and l.source_campaign_id
        )
        results = []

        for line in lines:
            ad_id = line.ad_id.strip()
            existing = AdMap.search([('ad_id', '=', ad_id)], limit=1)
            if existing:
                skipped += 1
                line.status = f'⚠️ Duplicate — already exists'
                results.append(f'⚠️  "{ad_id}" — already mapped')
                continue
            try:
                AdMap.create({
                    'adset_id':          line.adset_id.id,
                    'ad_id':             ad_id,
                    'source_campaign_id': line.source_campaign_id.id,
                })
                created += 1
                line.status = '✅ Created'
                results.append(
                    f'✅  "{ad_id}" → {line.source_campaign_id.name}'
                    f' ({line.adset_id.name})'
                )
            except Exception as e:
                errors += 1
                line.status = f'❌ Error: {e}'
                results.append(f'❌  "{ad_id}" — {e}')

        self.result_summary = (
            f'Created: {created}  |  Skipped (duplicate): {skipped}  |  Errors: {errors}\n\n'
            + '\n'.join(results)
        )
        self.state = 'done'

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_close(self):
        return {'type': 'ir.actions.act_window_close'}
