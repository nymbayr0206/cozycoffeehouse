# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    qpay_transaction_ids = fields.One2many(
        'qpay.transaction', 'sale_order_id',
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
        for order in self:
            order.qpay_transaction_count = len(order.qpay_transaction_ids)

    @api.depends('qpay_transaction_ids.state')
    def _compute_qpay_paid(self):
        for order in self:
            order.qpay_paid = any(
                t.state == 'paid' for t in order.qpay_transaction_ids
            )

    def action_create_qpay_transaction(self):
        """Захиалгаас QPay гүйлгээ үүсгэнэ."""
        self.ensure_one()
        if self.state not in ('sale', 'done'):
            raise UserError(_('Зөвхөн баталгаажсан захиалгад QPay гүйлгээ үүсгэх боломжтой.'))

        amount = self.amount_total
        if amount <= 0:
            raise UserError(_('Захиалгын дүн 0-ээс их байх ёстой.'))

        txn = self.env['qpay.transaction'].create({
            'sale_order_id': self.id,
            'partner_id': self.partner_id.id,
            'amount': amount,
            'currency_id': self.currency_id.id,
            'description': self.name or _('Захиалга'),
        })
        return txn.action_create_qpay_invoice()

    def action_view_qpay_transactions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('QPay Гүйлгээнүүд'),
            'res_model': 'qpay.transaction',
            'view_mode': 'list,form',
            'domain': [('sale_order_id', '=', self.id)],
            'context': {'default_sale_order_id': self.id},
        }
