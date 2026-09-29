from odoo import fields, models


class OtmSmSyncRow(models.Model):
    """Lean tracker: one tiny record per processed sheet row.

    * row_no      -> sheet row number (a synced row is never fetched again)
    * col_sig     -> 4-hex fingerprint per mapped column, so an edit in the
                     sheet updates ONLY the changed columns on the lead
    * key_hash    -> hash of phone(last 10 digits)::source_campaign
                     (same phone + same ad is processed once, forever)
    No create/write metadata columns are stored to keep the table small.
    """
    _name = 'otm.sm.sync.row'
    _description = 'Social Media Sync - Processed Row'
    _log_access = False

    config_id = fields.Many2one('otm.sm.sheet.config', required=True, index=True, ondelete='cascade')
    row_no = fields.Integer(required=True)
    col_sig = fields.Char(size=96)
    key_hash = fields.Char(size=12, index=True)
    error_msg = fields.Char(size=160)
    lead_id = fields.Integer(help='ID of the lead created from this row (plain integer, no FK).')
    result = fields.Selection([
        ('created', 'Lead Created'),
        ('re_enquiry', 'Re-Attempt Created'),
        ('duplicate', 'Skipped (same phone + ad)'),
        ('error', 'Error'),
    ], index=True)

    _config_row_uniq = models.Constraint('UNIQUE(config_id, row_no)', 'Row already tracked.')
