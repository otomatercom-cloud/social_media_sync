from odoo import fields, models


class OtmSmSyncedRecord(models.Model):
    """
    Tracks every processed row as  phone::ad_id  per config.

    Unique key = phone + ad_id (source_campaign_id)
    → One re-enquiry per lead per Ad, forever.
    → Even if the same phone appears in tomorrow's sheet with the same Ad ID,
      it is skipped — no duplicate re-enquiry ever created.
    → If the same phone appears with a DIFFERENT Ad ID (new ad/campaign),
      a new re-enquiry IS created because it's a genuinely new ad interaction.
    """
    _name        = 'otm.sm.synced.record'
    _description = 'Social Media Processed Row Tracker'

    config_id          = fields.Many2one(
        'otm.sm.sheet.config', string='Sheet Config',
        ondelete='cascade', required=True, index=True,
    )
    unique_key         = fields.Char(
        string='Unique Key (phone::ad_id)',
        required=True, index=True,
        help='Format: phone::source_campaign_id — uniquely identifies one lead+ad combination.',
    )
    phone              = fields.Char(string='Phone', index=True)
    source_campaign_id = fields.Integer(string='Source Campaign ID')
    synced_at          = fields.Datetime(default=fields.Datetime.now)
    result             = fields.Selection(
        [('created', 'Lead Created'),
         ('re_enquiry', 'Re-Enquiry Created')],
        string='Result',
    )

    _sql_constraints = [
        ('config_key_uniq', 'UNIQUE(config_id, unique_key)',
         'This phone+ad combination was already processed.'),
    ]
