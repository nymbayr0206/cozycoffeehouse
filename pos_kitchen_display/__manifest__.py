{
    'name': 'POS Kitchen Display',
    'version': '19.0.1.0.0',
    'summary': 'Real-time kitchen display screen for Point of Sale orders',
    'description': """
        Kitchen Display System for Odoo POS.
        - Real-time order display via WebSocket (bus.bus)
        - Auto-refresh every 5 seconds
        - Mark orders as In Progress / Done
        - Shows table number, product name, quantity
        - Fullscreen kitchen-optimized UI
    """,
    'category': 'Point of Sale',
    'author': 'Custom',
    'depends': ['point_of_sale', 'bus'],
    'data': [
        'security/kitchen_security.xml',
        'security/ir.model.access.csv',
        'views/kitchen_order_views.xml',
        'views/pos_config_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'pos_kitchen_display/static/src/css/kitchen_display.css',
            'pos_kitchen_display/static/src/components/kitchen_display.xml',
            'pos_kitchen_display/static/src/components/kitchen_display.js',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
