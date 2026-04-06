from odoo import models, fields


class PosConfig(models.Model):
    _inherit = 'pos.config'

    kitchen_display_enabled = fields.Boolean(
        string='Kitchen Display',
        help='Enable Kitchen Display System for this POS configuration.',
        default=False,
    )
    kitchen_display_auto_send = fields.Boolean(
        string='Auto-send orders to Kitchen',
        help='Automatically send orders to the kitchen display when confirmed.',
        default=True,
    )
    kitchen_display_alert_minutes = fields.Integer(
        string='Alert after (minutes)',
        help='Highlight orders on the kitchen display after this many minutes.',
        default=15,
    )
