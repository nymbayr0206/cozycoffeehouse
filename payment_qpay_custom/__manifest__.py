# -*- coding: utf-8 -*-
# QPay Payment Integration for Odoo 19
{
    'name': 'QPay Payment Integration',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Payment',
    'summary': 'QPay payment gateway integration (Mongolia)',
    'description': """
QPay Payment Gateway Integration
=================================
Integrates QPay (Mongolia's leading payment gateway) with Odoo.

Features:
- Create QPay invoices from Odoo customer invoices and sale orders
- QR code display for customer scanning
- Automatic and manual payment status polling
- Webhook/callback endpoint for real-time payment confirmation
- eBarimt (Mongolian e-receipt) creation after successful payment
- Full sandbox/production environment switching via settings
- Detailed API request/response logging for diagnostics
- Cron job for polling pending transactions
    """,
    'author': 'Custom Development',
    'website': '',
    'depends': [
        'base',
        'account',
        'sale',
        'mail',
        'point_of_sale',
        'pos_self_order',
    ],
    'data': [
        # Security first
        'security/qpay_security.xml',
        'security/ir.model.access.csv',
        # Data (sequences, cron)
        'data/qpay_data.xml',
        # Menus
        'views/qpay_menu.xml',
        # Model views
        'views/qpay_transaction_views.xml',
        'views/res_config_settings_views.xml',
        'views/account_move_views.xml',
        'views/sale_order_views.xml',
        # Wizard
        'wizard/qpay_qr_wizard_views.xml',
    ],
    'assets': {
        'pos_self_order.assets': [
            'payment_qpay_custom/static/src/js/pos_qpay_kiosk.js',
            'payment_qpay_custom/static/src/xml/pos_qpay_kiosk.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
    'post_init_hook': 'post_init_hook',
}
