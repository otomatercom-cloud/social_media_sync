from odoo.exceptions import UserError


def pre_init_hook(env):
    """Fail early with a readable message when custom_leads_19 is too old."""
    reg = env.registry
    missing = [m for m in ('lead.source.campaign', 'otomater.lead.reattempt', 'course.interested') if m not in reg]
    if not missing and 'source_campaign_id' not in reg['leads.logic']._fields:
        missing.append('leads.logic.source_campaign_id')
    if missing:
        raise UserError(
            "Social Media Sync needs the latest custom_leads_19 (v19.0.1.9.0 or newer). "
            "Missing on this server: %s.\nUpdate the custom_leads_19 code, upgrade that module "
            "(-u custom_leads_19), then install Social Media Sync." % ', '.join(missing))
