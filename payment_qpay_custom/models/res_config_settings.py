# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    qpay_environment = fields.Selection(
        [('sandbox', 'Sandbox (тест)'), ('production', 'Production (бодит)')],
        string='QPay орчин',
        default='sandbox',
        config_parameter='qpay.environment',
    )
    qpay_username = fields.Char(
        string='QPay нэвтрэх нэр',
        config_parameter='qpay.username',
    )
    qpay_password = fields.Char(
        string='QPay нууц үг',
        config_parameter='qpay.password',
    )
    qpay_invoice_code = fields.Char(
        string='QPay Invoice код',
        config_parameter='qpay.invoice_code',
    )
    qpay_callback_base_url = fields.Char(
        string='Callback URL (сервер хаяг)',
        help='Жишээ: https://yourdomain.com  — QPay энэ хаягт төлбөрийн мэдэгдэл илгээнэ.',
        config_parameter='qpay.callback_base_url',
    )
