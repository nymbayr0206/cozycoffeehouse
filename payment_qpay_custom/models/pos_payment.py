# -*- coding: utf-8 -*-
from odoo import api, fields, models


class PosPayment(models.Model):
    _inherit = "pos.payment"

    qpay_transaction_id = fields.Many2one(
        "qpay.transaction",
        string="QPay Transaction",
        ondelete="set null",
        readonly=True,
        copy=False,
    )

    @api.model
    def _load_pos_data_fields(self, config_id):
        result = super()._load_pos_data_fields(config_id)
        for field_name in [
            "transaction_id",
            "payment_status",
            "ticket",
            "card_type",
            "cardholder_name",
            "qpay_transaction_id",
        ]:
            if field_name not in result:
                result.append(field_name)
        return result
