from odoo import api, fields, models


class OtmSmSyncLog(models.Model):
    """One record per sync run that actually did something.

    Retention is 1 day (purged on every cron tick with a single indexed
    DELETE) so the table can never grow with big sheets.
    Runs that find no new rows are NOT logged.
    """
    _name = 'otm.sm.sync.log'
    _description = 'Social Media Sheet Sync Log'
    _order = 'sync_date desc'

    config_id = fields.Many2one('otm.sm.sheet.config', string='Sheet Config', ondelete='cascade', index=True)
    sync_date = fields.Datetime(string='Sync Date', default=fields.Datetime.now, readonly=True, index=True)
    rows_fetched = fields.Integer(string='Rows Read')
    leads_created = fields.Integer(string='Leads Created')
    leads_skipped = fields.Integer(string='Re-Attempts')
    leads_updated = fields.Integer(string='Leads Updated')
    leads_error = fields.Integer(string='Errors')
    state = fields.Selection([
        ('success', 'Success'), ('partial', 'Partial (some errors)'), ('failed', 'Failed'),
    ], default='success')
    error_details = fields.Text(string='Error Details')
    triggered_by = fields.Selection([('cron', 'Cron Job'), ('manual', 'Manual')], default='manual')

    @api.model
    def _purge_old(self, hours=24):
        self.env.cr.execute(
            "DELETE FROM otm_sm_sync_log WHERE sync_date < (NOW() AT TIME ZONE 'UTC') - %s * INTERVAL '1 hour'",
            (hours,))
