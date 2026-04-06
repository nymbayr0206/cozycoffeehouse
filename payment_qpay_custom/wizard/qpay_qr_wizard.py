# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class QpayQrWizard(models.TransientModel):
    _name = 'qpay.qr.wizard'
    _description = 'QPay QR Код харуулах'

    transaction_id = fields.Many2one(
        'qpay.transaction', string='Гүйлгээ', required=True,
    )
    qr_image = fields.Binary(related='transaction_id.qr_image', string='QR Код')
    qpay_short_url = fields.Char(related='transaction_id.qpay_short_url', string='QPay холбоос')
    amount = fields.Monetary(related='transaction_id.amount', string='Дүн')
    currency_id = fields.Many2one(related='transaction_id.currency_id')
    state = fields.Selection(related='transaction_id.state', string='Төлөв')

    def action_check_payment(self):
        self.ensure_one()
        return self.transaction_id.action_check_payment()

    def action_cancel(self):
        self.ensure_one()
        self.transaction_id.action_cancel()
        return {'type': 'ir.actions.act_window_close'}
