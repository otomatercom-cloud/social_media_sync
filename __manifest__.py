{
    'name': 'Social Media Sync',
    'version': '19.0.1.0.0',
    'summary': 'Google Sheet lead sync (new rows only) with Meta Ad ID to Source Campaign mapping and dashboard',
    'description': """
Social Media Sync
=================
* Reads a Google Sheet incrementally: only rows after the last synced one are downloaded.
* Edited cells in recent rows update ONLY the changed columns on the lead.
* Meta Campaign > Ad Set > Ad ID > Source Campaign mapping (with Excel import).
* Same phone + same ad is processed once; same phone with a new ad becomes a Re-Attempt.
* Sync logs are kept for 1 day only; the tracker keeps one tiny row per sheet row.
* Performance dashboard by campaign / ad set / ad.
    """,
    'author': 'Otomater',
    'website': 'https://otomater.com',
    'category': 'Marketing',
    'license': 'OPL-1',
    'depends': ['custom_leads_19'],
    'data': [
        'security/ir.model.access.csv',
        'data/cron.xml',
        'views/ad_mapping_views.xml',
        'views/sheet_config_views.xml',
        'views/sync_log_views.xml',
        'views/dashboard_views.xml',
        'views/test_connection_wizard_views.xml',
        'views/bulk_ad_wizard_views.xml',
        'views/excel_import_wizard_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'social_media_sync/static/src/css/dashboard.css',
            'social_media_sync/static/src/xml/dashboard.xml',
            'social_media_sync/static/src/js/dashboard.js',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
}
