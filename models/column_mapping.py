from odoo import fields, models, api

# All mappable fields from leads.logic
LEAD_FIELD_SELECTION = [
    # ── Core (required-ish) ──────────────────────────────────────────────────
    ('name',                    '👤 Lead Name'),
    ('phone_number',            '📱 Mobile / Phone'),
    ('email_address',           '📧 Email'),
    # ── Source / Campaign (auto-filled via Ad ID, but can override) ──────────
    ('ad_id_raw',               '🔖 Ad ID  (for campaign auto-map)'),
    # ── Course & Academic ────────────────────────────────────────────────────
    ('course_interested',       '📚 Course Interested'),
    ('academic_year',           '🗓️  Academic Year'),
    ('last_studied_course',     '🎓 Last Studied Course'),
    # ── Personal ────────────────────────────────────────────────────────────
    ('college_name',            '🏫 College / School'),
    ('country',                 '🌍 Country'),
    # ── Lead Classification ──────────────────────────────────────────────────
    ('lead_quality',            '⭐ Lead Quality'),
    ('incoming_source',         '📡 Incoming Source'),
    # ── Extra free-text fields ───────────────────────────────────────────────
    ('title',                   '🏷️  Title'),
    ('call_response',           '💬 Notes / Call Response'),
    # ── Officer Assignment ───────────────────────────────────────────────────
    ('lead_owner',              '🧑‍💼 Admission Officer (matched by name)'),
    # ── Timestamp (used only for filtering, not saved to lead) ──────────────
    ('_timestamp',              '🕐 Timestamp (filter only, not saved)'),
]

# Fields that are lookups (value in sheet = name to search)
LOOKUP_FIELDS = set()

# Fields that are pseudo / not direct model fields
PSEUDO_FIELDS = {'ad_id_raw', '_timestamp'}


class OtmSmColumnMapping(models.Model):
    """
    One row = one sheet-column → lead-field mapping.
    Lives as a One2many on otm.sm.sheet.config.
    """
    _name = 'otm.sm.column.mapping'
    _description = 'Social Media Sheet Column → Lead Field Mapping'
    _order = 'sequence, id'

    config_id = fields.Many2one(
        'otm.sm.sheet.config',
        string='Sheet Config',
        ondelete='cascade',
        required=True,
        index=True,
    )
    sequence = fields.Integer(default=10)

    sheet_column = fields.Char(
        string='Sheet Column Header',
        required=True,
        help='Exact header name from your Google Sheet row 1 (case-sensitive).',
    )
    lead_field = fields.Selection(
        LEAD_FIELD_SELECTION,
        string='Lead Field',
        required=True,
        help='Which field on the Lead this column maps to.',
    )
    lead_field_label = fields.Char(
        string='Maps To',
        compute='_compute_label',
        store=False,
    )
    is_required = fields.Boolean(
        string='Required?',
        help='If ticked and the sheet value is empty, the row will be skipped.',
    )
    default_value = fields.Char(
        string='Default Value',
        help='Used when the sheet cell is empty (optional).',
    )
    note = fields.Char(string='Note')

    @api.depends('lead_field')
    def _compute_label(self):
        label_map = dict(LEAD_FIELD_SELECTION)
        for rec in self:
            rec.lead_field_label = label_map.get(rec.lead_field, rec.lead_field or '')
