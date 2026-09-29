from datetime import timedelta

from odoo import api, fields, models


class OtmSmSheetConfigDashboard(models.Model):
    _inherit = 'otm.sm.sheet.config'

    # ── Dashboard data ────────────────────────────────────────────────────────
    @api.model
    def get_dashboard_data(self):
        """
        Fast dashboard data using raw SQL — single pass per query instead of
        dozens of search_count ORM calls.
        """
        cr    = self.env.cr
        today = fields.Date.today()
        month_start = today.replace(day=1)
        week_start  = today - timedelta(days=today.weekday())
        day14_start = today - timedelta(days=13)

        # ── 1. Collect relevant source_campaign_ids & source_ids ──────────
        cr.execute("""
            SELECT DISTINCT am.source_campaign_id, am.leads_source_id, am.adset_id
            FROM   otm_sm_ad_mapping am
            WHERE  am.active = true
              AND  am.source_campaign_id IS NOT NULL
        """)
        rows_map = cr.fetchall()
        uc_camp_ids   = list({r[0] for r in rows_map})
        uc_source_ids = list({r[1] for r in rows_map if r[1]})
        # default sources from configs
        cr.execute("SELECT default_leads_source_id FROM otm_sm_sheet_config WHERE active=true AND default_leads_source_id IS NOT NULL")
        uc_source_ids += [r[0] for r in cr.fetchall()]
        uc_source_ids  = list(set(uc_source_ids))

        if not uc_camp_ids:
            # No mappings yet — return empty structure
            return {
                'kpi': {'total':0,'today':0,'week':0,'month':0,'admissions_total':0,'admissions_month':0},
                'adset_perf': [], 'campaign_perf': [], 'best_performers': {},
                'daily_data': [], 'quality_data': {}, 'sync_logs': [],
                'total_leads': 0, 'leads_today': 0, 'leads_this_week': 0, 'leads_this_month': 0,
            }

        camp_tuple  = tuple(uc_camp_ids)
        source_tuple = tuple(uc_source_ids) if uc_source_ids else (0,)

        # ── 2. KPI counts — one query ──────────────────────────────────────
        cr.execute("""
            SELECT
                COUNT(*)                                                          AS total,
                COUNT(*) FILTER (WHERE date_of_adding = %s)                      AS today,
                COUNT(*) FILTER (WHERE date_of_adding >= %s)                     AS week,
                COUNT(*) FILTER (WHERE date_of_adding >= %s)                     AS month,
                COUNT(*) FILTER (WHERE lead_quality = 'admission')               AS adm_total,
                COUNT(*) FILTER (WHERE lead_quality = 'admission'
                                   AND date_of_adding >= %s)                     AS adm_month
            FROM leads_logic
            WHERE leads_source IN %s
        """, (today, week_start, month_start, month_start, source_tuple))
        kpi_row = cr.fetchone()
        kpi = {
            'total':             kpi_row[0] or 0,
            'today':             kpi_row[1] or 0,
            'week':              kpi_row[2] or 0,
            'month':             kpi_row[3] or 0,
            'admissions_total':  kpi_row[4] or 0,
            'admissions_month':  kpi_row[5] or 0,
        }

        # ── 3. Lead quality breakdown — one query ──────────────────────────
        cr.execute("""
            SELECT lead_quality, COUNT(*)
            FROM   leads_logic
            WHERE  leads_source IN %s
            GROUP  BY lead_quality
        """, (source_tuple,))
        quality_map = dict(cr.fetchall())
        quality_labels = {
            'new': 'New', 'first_attempt': 'First Attempt',
            'hot': 'Hot', 'warm': 'Warm', 'cold': 'Cold',
            'not_responding': 'Not Responding', 'follow_up': 'Follow Up',
            'admission': 'Admission', 'bad_lead': 'Language Barrier',
            'crash_lead': 'Crash Lead',
        }
        quality_data = {label: quality_map.get(key, 0) for key, label in quality_labels.items()}

        # ── 4. Daily trend last 14 days — one query ────────────────────────
        cr.execute("""
            SELECT
                date_of_adding,
                COUNT(*)                                          AS leads,
                COUNT(*) FILTER (WHERE lead_quality = 'admission') AS admissions
            FROM  leads_logic
            WHERE leads_source IN %s
              AND date_of_adding >= %s
            GROUP BY date_of_adding
        """, (source_tuple, day14_start))
        daily_map = {str(r[0]): {'leads': r[1], 'admissions': r[2]} for r in cr.fetchall()}
        daily_data = []
        for i in range(13, -1, -1):
            d = str(today - timedelta(days=i))
            daily_data.append({
                'date':       d,
                'leads':      daily_map.get(d, {}).get('leads', 0),
                'admissions': daily_map.get(d, {}).get('admissions', 0),
            })

        # ── 5. Adset performance — one query covering all periods ──────────
        cr.execute("""
            SELECT
                am.adset_id,
                COUNT(*)                                                              AS all_leads,
                COUNT(*) FILTER (WHERE l.lead_quality = 'admission')                 AS all_adm,
                COUNT(*) FILTER (WHERE l.lead_quality = 'hot')                       AS all_hot,
                COUNT(*) FILTER (WHERE l.lead_quality = 'warm')                      AS all_warm,
                COUNT(*) FILTER (WHERE l.date_of_adding = %(today)s)                 AS today_leads,
                COUNT(*) FILTER (WHERE l.date_of_adding = %(today)s
                                   AND l.lead_quality = 'admission')                  AS today_adm,
                COUNT(*) FILTER (WHERE l.date_of_adding = %(today)s
                                   AND l.lead_quality = 'hot')                        AS today_hot,
                COUNT(*) FILTER (WHERE l.date_of_adding = %(today)s
                                   AND l.lead_quality = 'warm')                       AS today_warm,
                COUNT(*) FILTER (WHERE l.date_of_adding >= %(week)s)                 AS week_leads,
                COUNT(*) FILTER (WHERE l.date_of_adding >= %(week)s
                                   AND l.lead_quality = 'admission')                  AS week_adm,
                COUNT(*) FILTER (WHERE l.date_of_adding >= %(week)s
                                   AND l.lead_quality = 'hot')                        AS week_hot,
                COUNT(*) FILTER (WHERE l.date_of_adding >= %(week)s
                                   AND l.lead_quality = 'warm')                       AS week_warm,
                COUNT(*) FILTER (WHERE l.date_of_adding >= %(month)s)                AS month_leads,
                COUNT(*) FILTER (WHERE l.date_of_adding >= %(month)s
                                   AND l.lead_quality = 'admission')                  AS month_adm,
                COUNT(*) FILTER (WHERE l.date_of_adding >= %(month)s
                                   AND l.lead_quality = 'hot')                        AS month_hot,
                COUNT(*) FILTER (WHERE l.date_of_adding >= %(month)s
                                   AND l.lead_quality = 'warm')                       AS month_warm
            FROM  otm_sm_ad_mapping am
            JOIN  leads_logic l ON l.source_campaign_id = am.source_campaign_id
            WHERE am.active = true
              AND am.adset_id IS NOT NULL
            GROUP BY am.adset_id
        """, {'today': today, 'week': week_start, 'month': month_start})

        adset_rows = cr.fetchall()

        # Fetch adset + campaign names in one query
        if adset_rows:
            adset_ids = [r[0] for r in adset_rows]
            cr.execute("""
                SELECT a.id, a.name, c.name
                FROM   otm_sm_meta_adset a
                LEFT JOIN otm_sm_meta_campaign c ON c.id = a.campaign_id
                WHERE  a.id IN %s
            """, (tuple(adset_ids),))
            adset_info = {r[0]: (r[1], r[2] or '—') for r in cr.fetchall()}
        else:
            adset_info = {}

        def _pdata(leads, adm, hot, warm):
            return {'leads': leads or 0, 'admissions': adm or 0,
                    'hot': hot or 0, 'warm': warm or 0}

        adset_perf = []
        for r in adset_rows:
            adset_id = r[0]
            name, camp = adset_info.get(adset_id, ('Unknown', '—'))
            adset_perf.append({
                'adset_id': adset_id,
                'adset':    name,
                'campaign': camp,
                'all':   _pdata(r[1],  r[2],  r[3],  r[4]),
                'today': _pdata(r[5],  r[6],  r[7],  r[8]),
                'week':  _pdata(r[9],  r[10], r[11], r[12]),
                'month': _pdata(r[13], r[14], r[15], r[16]),
            })
        adset_perf.sort(key=lambda x: -x['month']['leads'])

        # ── 6. Campaign rollup (in Python from adset data) ─────────────────
        camp_rollup = {}
        for row in adset_perf:
            c = row['campaign']
            if c not in camp_rollup:
                camp_rollup[c] = {
                    'campaign': c,
                    'today': {'leads': 0, 'admissions': 0},
                    'week':  {'leads': 0, 'admissions': 0},
                    'month': {'leads': 0, 'admissions': 0},
                    'all':   {'leads': 0, 'admissions': 0},
                }
            for p in ('today', 'week', 'month', 'all'):
                camp_rollup[c][p]['leads']      += row[p]['leads']
                camp_rollup[c][p]['admissions'] += row[p]['admissions']
        campaign_perf = sorted(camp_rollup.values(), key=lambda x: -x['month']['leads'])

        # ── 7. Best performers ─────────────────────────────────────────────
        def best(data, period, key):
            filtered = [r for r in data if r[period][key] > 0]
            return max(filtered, key=lambda x: x[period][key]) if filtered else None

        best_performers = {
            p: {'by_leads': best(adset_perf, p, 'leads'),
                'by_admissions': best(adset_perf, p, 'admissions')}
            for p in ('today', 'week', 'month', 'all')
        }

        # ── 8. Sync logs ───────────────────────────────────────────────────
        cr.execute("""
            SELECT l.sync_date, c.name, l.leads_created, l.leads_skipped,
                   l.leads_error, l.state
            FROM   otm_sm_sync_log l
            LEFT JOIN otm_sm_sheet_config c ON c.id = l.config_id
            ORDER  BY l.sync_date DESC
            LIMIT  8
        """)
        log_data = [{
            'date':    r[0].strftime('%d %b %Y %H:%M') if r[0] else '—',
            'config':  r[1] or '—',
            'created': r[2] or 0,
            'skipped': r[3] or 0,
            'errors':  r[4] or 0,
            'state':   r[5] or '—',
        } for r in cr.fetchall()]

        # ── 9. Sync summary — all-time counts come from the row tracker, ──
        #       runs come from the (1-day) log table
        cr.execute("""
            SELECT COUNT(*) FILTER (WHERE result = 'created'),
                   COUNT(*) FILTER (WHERE result = 're_enquiry'),
                   COUNT(*) FILTER (WHERE result = 'error')
            FROM otm_sm_sync_row
        """)
        sr = cr.fetchone()
        cr.execute("SELECT COUNT(*) FROM otm_sm_sync_log")
        runs = cr.fetchone()[0]
        sync_summary = {
            'total_created':    sr[0] or 0,
            'total_duplicates': sr[1] or 0,
            'total_errors':     sr[2] or 0,
            'total_runs':       runs or 0,
        }

        # ── 10. Per-Ad (Ad ID) lead breakdown ─────────────────────────────
        cr.execute("""
            SELECT
                am.ad_id,
                sc.name                                                           AS source_camp,
                ads.name                                                          AS adset,
                mc.name                                                           AS campaign,
                COUNT(l.id)                                                       AS total,
                COUNT(l.id) FILTER (WHERE l.lead_quality = 'admission')          AS admissions,
                COUNT(l.id) FILTER (WHERE l.lead_quality = 'hot')                AS hot,
                COUNT(l.id) FILTER (WHERE l.date_of_adding >= %(month)s)         AS month,
                COUNT(l.id) FILTER (WHERE l.date_of_adding >= %(week)s)          AS week,
                COUNT(l.id) FILTER (WHERE l.date_of_adding =  %(today)s)         AS today
            FROM  otm_sm_ad_mapping am
            LEFT JOIN lead_source_campaign sc  ON sc.id  = am.source_campaign_id
            LEFT JOIN otm_sm_meta_adset ads ON ads.id = am.adset_id
            LEFT JOIN otm_sm_meta_campaign mc ON mc.id = ads.campaign_id
            LEFT JOIN leads_logic l ON l.source_campaign_id = am.source_campaign_id
            WHERE am.active = true
            GROUP BY am.ad_id, sc.name, ads.name, mc.name
            ORDER BY total DESC
            LIMIT 100
        """, {'today': today, 'week': week_start, 'month': month_start})

        ad_perf = []
        for r in cr.fetchall():
            if (r[4] or 0) > 0:
                ad_perf.append({
                    'ad_id':       r[0] or '—',
                    'source_camp': r[1] or '—',
                    'adset':       r[2] or '—',
                    'campaign':    r[3] or '—',
                    'total':       r[4] or 0,
                    'admissions':  r[5] or 0,
                    'hot':         r[6] or 0,
                    'month':       r[7] or 0,
                    'week':        r[8] or 0,
                    'today':       r[9] or 0,
                })

        return {
            'kpi':             kpi,
            'sync_summary':    sync_summary,
            'adset_perf':      adset_perf,
            'campaign_perf':   campaign_perf,
            'ad_perf':         ad_perf,
            'best_performers': best_performers,
            'daily_data':      daily_data,
            'quality_data':    quality_data,
            'sync_logs':       log_data,
            # legacy keys
            'total_leads':      kpi['total'],
            'leads_today':      kpi['today'],
            'leads_this_week':  kpi['week'],
            'leads_this_month': kpi['month'],
        }
