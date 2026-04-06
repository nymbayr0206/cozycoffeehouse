# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = 'account.move'

    qpay_transaction_ids = fields.One2many(
        'qpay.transaction', 'invoice_id',
        string='QPay гүйлгээнүүд',
    )
    qpay_transaction_count = fields.Integer(
        string='QPay гүйлгээний тоо',
        compute='_compute_qpay_transaction_count',
    )
    qpay_paid = fields.Boolean(
        string='QPay-р төлөгдсөн',
        compute='_compute_qpay_paid',
        store=True,
    )

    @api.depends('qpay_transaction_ids')
    def _compute_qpay_transaction_count(self):
        for move in self:
            move.qpay_transaction_count = len(move.qpay_transaction_ids)

    @api.depends('qpay_transaction_ids.state')
    def _compute_qpay_paid(self):
        for move in self:
            move.qpay_paid = any(
                t.state == 'paid' for t in move.qpay_transaction_ids
            )

    def action_create_qpay_transaction(self):
        """Нэхэмжлэлээс QPay гүйлгээ үүсгэнэ."""
        self.ensure_one()
        if self.state != 'posted':
            raise UserError(_('Зөвхөн батлагдсан нэхэмжлэлд QPay гүйлгээ үүсгэх боломжтой.'))
        if self.move_type not in ('out_invoice', 'out_refund'):
            raise UserError(_('Зөвхөн борлуулалтын нэхэмжлэлд QPay гүйлгээ үүсгэх боломжтой.'))

        amount = self.amount_residual
        if amount <= 0:
            raise UserError(_('Төлбөрт дүн 0-ээс их байх ёстой.'))

        txn = self.env['qpay.transaction'].create({
            'invoice_id': self.id,
            'partner_id': self.partner_id.id,
            'amount': amount,
            'currency_id': self.currency_id.id,
            'description': self.name or self.ref or _('Нэхэмжлэл'),
        })
        return txn.action_create_qpay_invoice()

    def action_view_qpay_transactions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('QPay Гүйлгээнүүд'),
            'res_model': 'qpay.transaction',
            'view_mode': 'list,form',
            'domain': [('invoice_id', '=', self.id)],
            'context': {'default_invoice_id': self.id},
        }
