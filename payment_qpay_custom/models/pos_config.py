# -*- coding: utf-8 -*-

from odoo import models


class PosConfig(models.Model):
    _inherit = "pos.config"

    def _supported_kiosk_payment_terminal(self):
        terminals = list(super()._supported_kiosk_payment_terminal())
        if "qpay" not in terminals:
            terminals.append("qpay")
        return terminals
