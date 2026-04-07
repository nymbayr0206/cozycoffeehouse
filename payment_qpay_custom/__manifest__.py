# -*- coding: utf-8 -*-
{
    "name": "QPay Payment Integration",
    "version": "19.0.1.0.0",
    "category": "Accounting/Payment",
    "summary": "QPay payment gateway integration (Mongolia)",
    "description": """
QPay Payment Gateway Integration
================================
Integrates QPay with Odoo.

Features:
- Create QPay invoices from Odoo customer invoices and sale orders
- QR code display for customer scanning
- Automatic and manual payment status polling
- Webhook/callback endpoint for real-time payment confirmation
- Full sandbox/production environment switching via settings
- Detailed API request/response logging for diagnostics
- Cron job for polling pending transactions
    """,
    "author": "Custom Development",
    "website": "",
    "depends": [
        "base",
        "account",
        "sale",
        "mail",
        "point_of_sale",
        "pos_self_order",
    ],
    "data": [
        "security/qpay_security.xml",
        "security/ir.model.access.csv",
        "data/qpay_data.xml",
        "views/qpay_menu.xml",
        "views/qpay_transaction_views.xml",
        "views/res_config_settings_views.xml",
        "views/account_move_views.xml",
        "views/sale_order_views.xml",
        "wizard/qpay_qr_wizard_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "payment_qpay_custom/static/src/js/pos_qpay_terminal.js",
            "payment_qpay_custom/static/src/xml/pos_qpay_terminal.xml",
        ],
        "pos_self_order.assets": [
            "payment_qpay_custom/static/src/js/pos_qpay_kiosk.js",
            "payment_qpay_custom/static/src/xml/pos_qpay_kiosk.xml",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
    "license": "LGPL-3",
    "post_init_hook": "post_init_hook",
}
