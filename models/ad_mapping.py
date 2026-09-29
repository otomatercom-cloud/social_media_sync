from odoo import fields, models, api, _
from odoo.exceptions import ValidationError


# ─── Level 1: Meta Campaign ───────────────────────────────────────────────────
class OtmSmMetaCampaign(models.Model):
    """
    Top-level Meta Campaign (e.g. "CA Weekend May 2025").
    One campaign has many Ad Sets.
    """
    _name        = 'otm.sm.meta.campaign'
    _description = 'Meta Campaign'
    _order       = 'name'

    name       = fields.Char(string='Campaign Name', required=True)
    active     = fields.Boolean(default=True)
    note       = fields.Text(string='Notes')

    adset_ids  = fields.One2many('otm.sm.meta.adset', 'campaign_id', string='Ad Sets')
    adset_count = fields.Integer(string='Ad Sets', compute='_compute_adset_count')

    def _compute_adset_count(self):
        for rec in self:
            rec.adset_count = len(rec.adset_ids)

    def action_view_adsets(self):
        self.ensure_one()
        return {
            'name': _('Ad Sets — %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'otm.sm.meta.adset',
            'view_mode': 'list,form',
            'domain': [('campaign_id', '=', self.id)],
            'context': {'default_campaign_id': self.id},
        }


# ─── Level 2: Meta Ad Set ────────────────────────────────────────────────────
class OtmSmMetaAdset(models.Model):
    """
    Mid-level Meta Ad Set (e.g. "Ernakulam 18-35 Audience").
    Belongs to one Campaign, has many Ads (Ad IDs).
    """
    _name        = 'otm.sm.meta.adset'
    _description = 'Meta Ad Set'
    _order       = 'campaign_id, name'

    name        = fields.Char(string='Ad Set Name', required=True)
    campaign_id = fields.Many2one(
        'otm.sm.meta.campaign', string='Campaign', required=True, ondelete='cascade'
    )
    active      = fields.Boolean(default=True)
    note        = fields.Text(string='Notes')

    ad_ids      = fields.One2many('otm.sm.ad.mapping', 'adset_id', string='Ads')
    ad_count    = fields.Integer(string='Ads', compute='_compute_ad_count')

    def _compute_ad_count(self):
        for rec in self:
            rec.ad_count = len(rec.ad_ids)

    def action_view_ads(self):
        self.ensure_one()
        return {
            'name': _('Ads — %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'otm.sm.ad.mapping',
            'view_mode': 'list,form',
            'domain': [('adset_id', '=', self.id)],
            'context': {'default_adset_id': self.id},
        }


# ─── Level 3: Ad (Ad ID → Source Campaign) ───────────────────────────────────
class OtmSmAdMapping(models.Model):
    """
    Individual Meta Ad — has the Ad ID from the sheet.
    Maps to an Odoo Source Campaign (lead.source.campaign).

    Hierarchy: Meta Campaign → Ad Set → Ad → Source Campaign (Odoo)
    """
    _name        = 'otm.sm.ad.mapping'
    _description = 'Meta Ad → Source Campaign Mapping'
    _order       = 'adset_id, ad_id'

    # ── Meta hierarchy ────────────────────────────────────────────────────
    adset_id = fields.Many2one(
        'otm.sm.meta.adset', string='Ad Set',
        required=True, ondelete='cascade',
    )
    campaign_id = fields.Many2one(
        'otm.sm.meta.campaign', string='Campaign',
        related='adset_id.campaign_id', store=True, readonly=True,
    )

    # ── Ad identifier ─────────────────────────────────────────────────────
    ad_id = fields.Char(
        string='Ad ID',
        required=True, index=True,
        help='Exact ad_id value from the Google Sheet (case-sensitive).',
    )

    # ── Odoo mapping ──────────────────────────────────────────────────────
    source_campaign_id = fields.Many2one(
        'lead.source.campaign',
        string='Source Campaign',
        required=True,
        ondelete='restrict',
        help='The Odoo Source Campaign this ad maps to.',
    )
    leads_source_id = fields.Many2one(
        'leads.sources',
        string='Lead Source (auto)',
        related='source_campaign_id.lead_source_id',
        store=True, readonly=True,
    )

    active = fields.Boolean(default=True)
    note   = fields.Text(string='Notes')

    _ad_id_uniq = models.Constraint('UNIQUE(ad_id)', 'Each Ad ID must be unique.')

    @api.constrains('ad_id')
    def _check_ad_id(self):
        for rec in self:
            if not (rec.ad_id or '').strip():
                raise ValidationError(_('Ad ID cannot be empty.'))
