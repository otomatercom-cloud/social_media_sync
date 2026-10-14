from odoo import api, fields, models


class OtmSmLeadIssue(models.Model):
    """A sheet value that could NOT be saved on a lead created by the sync
    (owner not found, value not in a selection, column missing, course not found...)."""
    _name = 'otm.sm.lead.issue'
    _description = 'Social Media Sync - Lead Issue'
    _order = 'id desc'

    config_id = fields.Many2one('otm.sm.sheet.config', string='Sheet Config', ondelete='cascade', index=True)
    row_no = fields.Integer(string='Sheet Row')
    lead_id = fields.Many2one('leads.logic', string='Lead', ondelete='cascade', index=True)
    field_name = fields.Char(string='Field Key', index=True)
    field_label = fields.Char(string='Lead Field')
    sheet_column = fields.Char(string='Sheet Column')
    sheet_value = fields.Char(string='Sheet Value')
    reason = fields.Char(string='Issue')
    state = fields.Selection([('open', 'Open'), ('resolved', 'Resolved')], default='open', index=True)

    # -- dashboard ---------------------------------------------------------------
    @api.model
    def _auto_resolve(self):
        """An issue is resolved as soon as the lead field has been filled in by hand."""
        for iss in self.sudo().search([('state', '=', 'open')], limit=2000):
            lead = iss.lead_id
            if not lead or (iss.field_name in lead._fields and lead[iss.field_name]):
                iss.state = 'resolved'

    @api.model
    def get_issue_dashboard_data(self, config_id=False, field_name=False, state='open', search=False):
        self._auto_resolve()
        cr = self.env.cr
        cr.execute("""
            SELECT COUNT(*) FILTER (WHERE state = 'open'),
                   COUNT(*) FILTER (WHERE state = 'resolved'),
                   COUNT(DISTINCT lead_id) FILTER (WHERE state = 'open'),
                   COUNT(*) FILTER (WHERE state = 'open' AND create_date >= date_trunc('day', NOW() AT TIME ZONE 'UTC'))
            FROM otm_sm_lead_issue
        """)
        o, r, la, t = cr.fetchone()
        cr.execute("""SELECT field_name, MAX(field_label), COUNT(*) FROM otm_sm_lead_issue
                      WHERE state = 'open' GROUP BY field_name ORDER BY 3 DESC""")
        by_field = [{'key': k, 'label': l or k, 'count': c} for k, l, c in cr.fetchall()]
        cr.execute("""SELECT reason, COUNT(*) FROM otm_sm_lead_issue
                      WHERE state = 'open' GROUP BY reason ORDER BY 2 DESC LIMIT 8""")
        by_reason = [{'reason': k, 'count': c} for k, c in cr.fetchall()]
        configs = [{'id': c.id, 'name': c.name} for c in self.env['otm.sm.sheet.config'].search([])]

        domain = []
        if state in ('open', 'resolved'):
            domain.append(('state', '=', state))
        if config_id:
            domain.append(('config_id', '=', int(config_id)))
        if field_name:
            domain.append(('field_name', '=', field_name))
        if search:
            domain += ['|', '|', ('sheet_value', 'ilike', search), ('reason', 'ilike', search),
                       ('lead_id.name', 'ilike', search)]
        recs = self.search(domain, limit=300)
        phones = {l['id']: l['phone_number'] for l in
                  recs.lead_id.read(['phone_number'])} if recs.lead_id else {}
        rows = [{
            'id': i.id, 'lead_id': i.lead_id.id, 'lead_name': i.lead_id.name,
            'phone': phones.get(i.lead_id.id, ''), 'config': i.config_id.name, 'row_no': i.row_no,
            'field': i.field_label, 'column': i.sheet_column, 'value': i.sheet_value,
            'reason': i.reason, 'state': i.state,
            'date': fields.Datetime.to_string(i.create_date) if i.create_date else '',
        } for i in recs]
        return {'kpi': {'open': o, 'resolved': r, 'leads': la, 'today': t},
                'by_field': by_field, 'by_reason': by_reason, 'configs': configs, 'rows': rows}

    def action_resolve(self):
        self.write({'state': 'resolved'})
        return True

    def action_reopen(self):
        self.write({'state': 'open'})
        return True
